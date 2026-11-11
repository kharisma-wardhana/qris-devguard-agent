"""Token bucket and the pre-auth per-address middleware (p7 plan step 1; spec Behaviour §2)."""

from __future__ import annotations

import json

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.security import security
from app.security.ratelimit import PerAddressLimit, TokenBucket, retry_after


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_burst_then_refused_then_refills():
    clock = Clock()
    b = TokenBucket(per_minute=60, burst=3, clock=clock)            # 1 token per second
    assert [b.take("k") for _ in range(3)] == [0.0, 0.0, 0.0]
    wait = b.take("k")
    assert wait == pytest.approx(1.0)
    clock.t += 0.5
    assert b.take("k") == pytest.approx(0.5)                        # half a token refilled, still short
    clock.t += 0.5
    assert b.take("k") == 0.0


def test_refill_never_exceeds_burst():
    clock = Clock()
    b = TokenBucket(per_minute=60, burst=2, clock=clock)
    b.take("k")
    clock.t += 3600
    assert [b.take("k") for _ in range(3)][:2] == [0.0, 0.0]
    assert b.take("k") > 0


def test_keys_are_independent():
    b = TokenBucket(per_minute=1, burst=1, clock=Clock())
    assert b.take("alice") == 0.0
    assert b.take("alice") > 0
    assert b.take("bob") == 0.0


def test_key_count_is_bounded():
    b = TokenBucket(per_minute=60, burst=1, max_keys=5, clock=Clock())
    for i in range(50):
        b.take(f"k{i}")
    assert len(b) == 5


@pytest.mark.parametrize("args", [(0, 1), (60, 0), (-1, 5)])
def test_invalid_limits_rejected(args):
    with pytest.raises(ValueError):
        TokenBucket(*args)


def test_config_values_match_spec():
    rate = security()["limits"]["rate"]
    assert rate["api_agent_run"] == {"per_minute": 10, "burst": 5}
    assert rate["api_other"] == {"per_minute": 60, "burst": 20}
    assert rate["mcp_tool_call"] == {"per_minute": 120, "burst": 40}
    assert rate["per_address"]["per_minute"] == 300
    for name in ("per_address", "api_agent_run", "api_other", "mcp_tool_call"):
        TokenBucket.from_config(name)                               # loads without error


def test_retry_after_is_whole_seconds_at_least_one():
    assert retry_after(0.01) == "1"
    assert retry_after(2.2) == "3"


def _app(bucket, refused):
    inner = Starlette(routes=[Route("/x", lambda r: PlainTextResponse("ok"), methods=["GET", "POST"]),
                              Route("/health/live", lambda r: PlainTextResponse("live"))])
    return PerAddressLimit(inner, bucket=bucket, on_refuse=refused.append)


def test_middleware_answers_429_envelope_after_burst():
    refused: list[str] = []
    client = TestClient(_app(TokenBucket(per_minute=1, burst=2, clock=Clock()), refused))
    assert [client.get("/x").status_code for _ in range(2)] == [200, 200]
    r = client.post("/x", content=b"secret-shaped-body-ZZZ")
    assert r.status_code == 429
    body = r.json()
    assert body["code"] == "RATE_LIMITED" and set(body) == {"code", "message", "correlation_id"}
    assert int(r.headers["retry-after"]) >= 1
    assert "ZZZ" not in r.text
    assert refused == ["RATE_LIMITED"]


def test_health_routes_are_exempt():
    client = TestClient(_app(TokenBucket(per_minute=1, burst=1, clock=Clock()), []))
    client.get("/x")
    assert [client.get("/health/live").status_code for _ in range(5)] == [200] * 5
    assert json.loads(client.get("/x").text)["code"] == "RATE_LIMITED"
