"""The agent's MCP client against the real P4 MCP app in the harness (p5 spec AC #6, #7)."""

from __future__ import annotations

import asyncio

import jwt
import pytest

from app.agent import config
from app.agent.harness import McpHarness, free_port
from app.agent.tools import ToolClient
from app.mcp_server import config as mcp_config


@pytest.fixture(scope="module")
def harness():
    with McpHarness() as h:
        yield h


def run(client, role, calls, sub="dev-1"):
    return asyncio.run(client.call(sub, role, calls))


SIG = ("validate_test_signature", {"algorithm": "SHA256withRSA", "fixture_id": "syn-rsa-01",
                                   "canonicalization_profile": "snap-asymmetric-access-token-v1",
                                   "test_payload_ref": "sig-at-wrong-separator"})


def test_each_tool_through_the_server(harness):
    client = ToolClient(harness.url, harness.secret)
    calls = [
        ("search_qris_spec", {"query": "QR validity period minutes", "top_k": 5}),
        ("validate_request_payload", {"api_family": "qris-mpm", "endpoint_id": "qr-mpm-query", "spec_version": "0.1.0",
                                      "payload": {"headers": {}, "body": {}}}),
        SIG,
        ("lookup_response_code", {"code": "4044700", "api_family": "qris-mpm", "spec_version": "0.1.0"}),
    ]
    search, payload, sig, code = run(client, "developer", calls)
    assert search.ok and {r["document_version"] for r in search.data["results"]
                          if r["document_id"] == "synthetic-qr-validity"} == {"0.1.0", "0.2.0"}
    assert payload.ok and payload.data["valid"] is False
    assert sig.ok and (sig.data["status"], sig.data["diagnostic_code"]) == ("INVALID", "SIG_WRONG_SEPARATOR")
    assert code.ok and code.data["status"] == "NOT_FOUND"
    assert all(r.ms >= 0 for r in (search, payload, sig, code))


def test_role_matrix_applies_to_agent_calls(harness):
    (res,) = run(ToolClient(harness.url, harness.secret), "kb_maintainer", [SIG])
    assert not res.ok and res.error_code == "FORBIDDEN"


def test_minted_token_is_mcp_audience_short_lived_for_the_caller(harness):
    client = ToolClient(harness.url, harness.secret)
    claims = jwt.decode(client.mint("dev-7", "support_engineer"), options={"verify_signature": False})
    assert claims["aud"] == mcp_config.mcp()["auth"]["audience"] != config.agent()["api"]["audience"]
    assert (claims["sub"], claims["role"]) == ("dev-7", "support_engineer")
    assert claims["exp"] - claims["iat"] == config.agent()["tools"]["token_ttl_minutes"] * 60


def test_unreachable_server_gives_tool_unavailable_after_bounded_retry(caplog):
    client = ToolClient(f"http://127.0.0.1:{free_port()}/mcp", "s" * 48)
    client.timeout = 2
    (res,) = run(client, "developer", [SIG])
    assert not res.ok and res.error_code == "TOOL_UNAVAILABLE"
    assert caplog.text.count("mcp session failed") == 1 + config.agent()["tools"]["transient_retries"]


def test_envelope_is_not_retried(harness, caplog):
    client = ToolClient(harness.url, harness.secret)
    (res,) = run(client, "developer", [("lookup_response_code", {"code": "x", "api_family": "snap", "spec_version": "1.0.2"})])
    assert not res.ok and res.error_code == "INVALID_ARGUMENT"
    assert "mcp session failed" not in caplog.text


def test_client_repr_hides_secret(harness):
    assert harness.secret not in repr(ToolClient(harness.url, harness.secret))
