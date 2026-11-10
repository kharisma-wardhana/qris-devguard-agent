"""FastAPI app with TestClient over the MCP harness and the fake LLM (p5 spec AC #1, #2, #17)."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.agent import answer, config
from app.agent.graph import Agent
from app.agent.harness import McpHarness
from app.agent.llm import FakeLLM
from app.agent.tools import ToolClient
from app.api.app import create_app
from app.mcp_server import auth

API_AUD = config.agent()["api"]["audience"]


async def up() -> bool:
    return True


async def down() -> bool:
    return False


@pytest.fixture(scope="module")
def harness():
    with McpHarness() as h:
        yield h


@pytest.fixture(scope="module")
def client(harness):
    app = create_app(agent=Agent(ToolClient(harness.url, harness.secret), FakeLLM()), secret=harness.secret,
                     mcp_ping=up, llm_ping=up)
    return TestClient(app, raise_server_exceptions=False)


def token(harness, role="developer", audience=API_AUD, **kw):
    return auth.mint("dev-1", role, secret=kw.pop("secret", harness.secret), audience=audience, **kw)


def post(client, tok, body):
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    return client.post("/api/v1/chat", json=body, headers=headers)


CODE = {"message": "what does 4044700 mean?", "context": {"api_family": "qris-mpm", "spec_version": "0.1.0",
                                                          "response_code": "4044700"}}


def test_answer_shape_and_unique_ids(client, harness):
    r1, r2 = post(client, token(harness), CODE), post(client, token(harness), CODE)
    assert r1.status_code == r2.status_code == 200
    a, b = r1.json(), r2.json()
    assert set(a) == {"run_id", "thread_id", "intent", "risk_level", "answer"} and set(answer.SECTIONS) <= set(a["answer"])
    assert a["run_id"] != b["run_id"] and a["thread_id"] != b["thread_id"]
    assert a["answer"]["validation_results"][0]["result"] == "NOT_FOUND"


@pytest.mark.parametrize("name", ["none", "garbage", "mcp audience", "expired", "other secret", "basic scheme"])
def test_unauthenticated_gets_401_envelope(client, harness, name):
    tok = {"none": None, "garbage": "not.a.jwt", "mcp audience": token(harness, audience=None),
           "expired": token(harness, ttl_minutes=1, now=int(time.time()) - 3600),
           "other secret": token(harness, secret="o" * 48), "basic scheme": None}[name]
    if name == "basic scheme":
        r = client.post("/api/v1/chat", json=CODE, headers={"Authorization": "Basic ZGV2OnB3"})
    else:
        r = post(client, tok, CODE)
    assert r.status_code == 401 and set(r.json()) == {"code", "message", "correlation_id"}
    assert r.json()["code"] == "UNAUTHENTICATED" and r.headers.get("www-authenticate") == "Bearer"


@pytest.mark.parametrize("body,field", [
    ({**CODE, "extra": 1}, "extra"),
    ({"message": "x" * (config.agent()["api"]["max_message_chars"] + 1)}, "message"),
    ({"message": ""}, "message"),
    ({"message": "q", "context": {"payload": {"body": {"x": "Z" * 33000}}}}, "context.payload"),
    ({"message": "q", "context": {"response_code": "../../etc"}}, "context.response_code"),
    ({"message": "q", "context": {"signature": {"fixture_id": "http://x"}}}, "context.signature.fixture_id"),
    ({"message": "q", "context": {"endpoint_id": "payment-refund"}}, "context.endpoint_id"),
])
def test_malformed_body_gets_422_without_values(client, harness, body, field):
    r = post(client, token(harness), body)
    assert r.status_code == 422 and r.json()["code"] == "INVALID_ARGUMENT"
    assert field in [e["field"] for e in r.json()["field_errors"]]
    text = json.dumps(r.json())
    assert "etc" not in text and "http://x" not in text and "ZZZZ" not in text and "Traceback" not in text


def test_unknown_route_is_envelope(client):
    r = client.get("/api/v1/nope")
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"


def test_health_live_and_ready(client, harness):
    assert client.get("/health/live").json() == {"status": "live"}
    assert client.get("/health/ready").json() == {"status": "ready", "checks": {"mcp": "up", "llm": "up"}}
    for mcp, llm, checks in ((down, up, {"mcp": "down", "llm": "up"}), (up, down, {"mcp": "up", "llm": "down"})):
        app = create_app(agent=None, secret=harness.secret, mcp_ping=mcp, llm_ping=llm)
        r = TestClient(app).get("/health/ready")
        assert r.status_code == 503 and r.json()["checks"] == checks
        assert "127.0.0.1" not in r.text and harness.secret not in r.text


def test_request_log_has_no_message_text(client, harness, caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="devguard.api"):
        post(client, token(harness), {"message": "UNIQUE-SYN-MESSAGE-TEXT what is 4044700?", "context": CODE["context"]})
    assert "route=/api/v1/chat" in caplog.text and "UNIQUE-SYN-MESSAGE-TEXT" not in caplog.text


def test_internal_error_is_safe(harness):
    class Boom:
        async def run(self, **kw):
            raise RuntimeError("secret detail /etc/x")
    r = TestClient(create_app(agent=Boom(), secret=harness.secret, mcp_ping=up, llm_ping=up),
                   raise_server_exceptions=False).post("/api/v1/chat", json=CODE,
                                                       headers={"Authorization": f"Bearer {token(harness)}"})
    assert r.status_code == 500 and r.json()["code"] == "INTERNAL_ERROR" and "secret detail" not in r.text
