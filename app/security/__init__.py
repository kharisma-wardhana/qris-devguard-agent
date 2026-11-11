"""Cross-cutting controls shared by the API and the MCP server (p7-mcp-hardening; TRD §24 layout).

- `ratelimit`: token-bucket limiter and the pre-auth per-address middleware (T12).
- `body_limit`: request-size middleware (413 before parsing).
- `egress`: start-up check of configured outbound destinations against `egress_allowlist` (T9).

Refusals made here are raw ASGI responses carrying the §18 envelope (`code`, `message`, `correlation_id`),
built by `send_envelope`. They never echo request bytes.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]

MESSAGES = {
    "RATE_LIMITED": "too many requests; retry after the number of seconds in Retry-After",
    "PAYLOAD_TOO_LARGE": "the request body is larger than allowed",
}

Send = Callable[[dict[str, Any]], Awaitable[None]]


@lru_cache(maxsize=1)
def security() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "configs" / "security.yaml").read_text(encoding="utf-8"))


def envelope_body(code: str, correlation_id: str | None = None) -> dict[str, Any]:
    return {"code": code, "message": MESSAGES[code], "correlation_id": correlation_id or uuid.uuid4().hex}


async def send_envelope(send: Send, status: int, code: str, headers: dict[str, str] | None = None) -> None:
    body = json.dumps(envelope_body(code)).encode("utf-8")
    raw = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    raw += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    await send({"type": "http.response.start", "status": status, "headers": raw})
    await send({"type": "http.response.body", "body": body})
