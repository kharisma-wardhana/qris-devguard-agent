"""Token-bucket rate limiting (p7 spec Behaviour §2; T12).

- `TokenBucket`: one bucket per key, `burst` tokens, refilled at `per_minute / 60` tokens per second.
  `take(key)` returns 0.0 when the call is allowed, else the seconds until a token is available.
  In-process and thread-safe; the clock is injectable for tests; at most `max_keys` keys are kept (the least
  recently used key is evicted, which only ever makes a limiter more lenient, never stricter).
- `PerAddressLimit`: pure-ASGI middleware that applies a bucket keyed by client address *before*
  authentication and answers HTTP 429 with the §18 envelope and `Retry-After`. Health routes are exempt.

Limits are per process and reset on restart: acceptable for one loopback process (spec Flagged concerns).
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from app.security import send_envelope, security


class TokenBucket:
    def __init__(self, per_minute: float, burst: int, *, max_keys: int = 10000,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if per_minute <= 0 or burst < 1 or max_keys < 1:
            raise ValueError("per_minute and burst must be positive")
        self.rate = per_minute / 60.0
        self.burst = float(burst)
        self.max_keys = max_keys
        self.clock = clock
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()   # key -> (tokens, last time)
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, name: str, *, clock: Callable[[], float] = time.monotonic) -> TokenBucket:
        rate = security()["limits"]["rate"]
        return cls(rate[name]["per_minute"], rate[name]["burst"], max_keys=rate["max_keys"], clock=clock)

    def take(self, key: str) -> float:
        with self._lock:
            now = self.clock()
            tokens, last = self._buckets.pop(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens >= 1.0:
                tokens, wait = tokens - 1.0, 0.0
            else:
                wait = (1.0 - tokens) / self.rate
            self._buckets[key] = (tokens, now)
            while len(self._buckets) > self.max_keys:
                self._buckets.popitem(last=False)
            return wait

    def __len__(self) -> int:
        return len(self._buckets)


def retry_after(wait: float) -> str:
    return str(max(1, math.ceil(wait)))


class PerAddressLimit:
    """Pre-auth limit by client address. `on_refuse(code)` lets the MCP server audit the refusal."""

    EXEMPT = ("/health/live", "/health/ready")

    def __init__(self, app: Any, bucket: TokenBucket | None = None,
                 on_refuse: Callable[[str], None] | None = None) -> None:
        self.app = app
        # `is None`, not `or`: an empty TokenBucket has len() 0 and is falsy
        self.bucket = bucket if bucket is not None else TokenBucket.from_config("per_address")
        self.on_refuse = on_refuse

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path") in self.EXEMPT:
            await self.app(scope, receive, send)
            return
        client = scope.get("client") or ("unknown", 0)
        wait = self.bucket.take(f"addr:{client[0]}")
        if wait > 0:
            if self.on_refuse:
                self.on_refuse("RATE_LIMITED")
            await send_envelope(send, 429, "RATE_LIMITED", {"Retry-After": retry_after(wait)})
            return
        await self.app(scope, receive, send)
