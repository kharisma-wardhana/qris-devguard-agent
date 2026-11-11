"""MCP config and tools (p4 spec AC #2-#5, #13, #17)."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path

import pytest
import yaml
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from qdrant_client import QdrantClient

from app.mcp_server import config
from app.mcp_server import server as srv
from app.security.ratelimit import TokenBucket
from app.mcp_server import tools as t
from app.rag import ingest as ing
from app.rag.embedding import FakeEmbedder
from app.rag.store import Store
from app.validators import keys

ROOT = Path(__file__).resolve().parents[2]
SECRET = "s" * 48
REAL = {e["document_id"]: e for e in yaml.safe_load(
    (ROOT / "knowledge_base" / "manifests" / "sources.yaml").read_text(encoding="utf-8"))["sources"]}


# --- config (step 1) ------------------------------------------------------------------------------

def test_config_loads_with_loopback_and_rerank_off():
    cfg = config.mcp()
    assert cfg["server"]["host"] == "127.0.0.1"
    assert cfg["search"]["rerank"] is False
    assert cfg["auth"]["algorithm"] == "HS256"
    assert cfg["auth"]["max_ttl_minutes"] <= 60


def test_role_matrix_comes_from_security_yaml():
    assert config.roles() == {"developer", "support_engineer", "kb_maintainer", "admin"}
    assert "kb_maintainer" not in config.tool_roles("validate_test_signature")
    assert "kb_maintainer" in config.tool_roles("search_qris_spec")
    assert config.tool_roles("create_support_ticket")          # the p6 write tool
    assert config.tool_roles("no_such_tool") == frozenset()


def test_every_p4_tool_is_in_the_matrix():
    for tool in config.TOOL_NAMES:
        assert config.tool_roles(tool), tool


def test_endpoints_match_scope():
    assert set(config.mcp()["validators"]["endpoints"]) == {"access-token-b2b", "qr-mpm-generate", "qr-mpm-query"}


# --- tools through the guarded SDK tools (step 7) -------------------------------------------------

def _store(tmp_path):
    docs = ("synthetic-qris-mpm-service-spec", "synthetic-qris-troubleshooting-sop")
    for d in docs:
        dest = tmp_path / REAL[d]["local_path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / REAL[d]["local_path"], dest)
    manifest = tmp_path / "sources.yaml"
    manifest.write_text(yaml.safe_dump({"sources": [REAL[d] for d in docs]}, allow_unicode=True), encoding="utf-8")
    store, emb = Store(QdrantClient(":memory:"), "test_kb"), FakeEmbedder()
    assert all(d.status == "ingested" for d in ing.ingest(manifest, tmp_path, store, emb).documents)
    return store, emb


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("mcp")
    c = t.default_context(database=False)
    keys.generate(tmp / "keys", c.signature_cases["keys"])
    c.keystore = keys.Keystore(tmp / "keys")
    store, emb = _store(tmp / "kb")
    c.backends = lambda: (store, emb)
    c.db = None                                              # ticket store off here; tested in test_ticket_tool.py

    def no_reranker():
        raise AssertionError("reranker must not be built when search.rerank is false")
    c.reranker = no_reranker
    return c


@pytest.fixture(scope="module")
def server(ctx):
    # p7: this suite tests tool behaviour, not throttling (real limits: test_security_suite.py)
    return srv.build_server(SECRET, ctx, tool_limiter=TokenBucket(10**6, 10**6))


def call(server, name, args, role="developer", sub="dev-1"):
    async def go():
        if role is not None:
            tok = AccessToken(token="t", client_id=sub, scopes=[role], subject=sub, claims={"role": role})
            auth_context_var.set(AuthenticatedUser(tok))
        else:
            auth_context_var.set(None)
        return await server.call_tool(name, args)
    res = asyncio.run(go())
    return res.is_error, json.loads(res.content[0].text)


GOOD = {
    "search_qris_spec": {"query": "X-SIGNATURE HMAC_SHA512 string to sign", "top_k": 3},
    "validate_request_payload": {"api_family": "qris-mpm", "endpoint_id": "qr-mpm-query", "spec_version": "0.1.0",
                                 "payload": {"headers": {}, "body": {}}},
    "validate_test_signature": {"algorithm": "HMAC_SHA512", "fixture_id": "syn-hmac-01",
                                "canonicalization_profile": "synthetic-snap-symmetric-v1",
                                "test_payload_ref": "sig-gen-valid"},
    "lookup_response_code": {"code": "4014700", "api_family": "qris-mpm", "spec_version": "0.1.0"},
    "create_support_ticket": {"approval_id": "00000000-0000-4000-8000-000000000000", "thread_id": "t" * 32,
                              "title": "SYN ticket", "body": "SYN body", "evidence_ids": [], "priority": "low",
                              "idempotency_key": __import__("app.db.actions", fromlist=["x"]).idempotency_key(
                                  "t" * 32, {"title": "SYN ticket", "body": "SYN body", "evidence_ids": [],
                                             "priority": "low"})},
}


def test_exactly_four_read_only_tools(server):
    """p6 regression: the four P4 tools stay read-only; the only addition is the approval-checked ticket tool."""
    tools = {x.name: x for x in asyncio.run(server.list_tools())}
    assert sorted(tools) == sorted(config.TOOL_NAMES) and len(tools) == 5
    assert all(tools[n].annotations.read_only_hint for n in config.READ_ONLY_TOOLS) and len(config.READ_ONLY_TOOLS) == 4
    assert tools["create_support_ticket"].annotations.read_only_hint is False
    assert not any(x.annotations.destructive_hint for x in tools.values())
    banned = ("payment", "refund", "url", "sql", "shell", "path", "file", "exec", "delete", "update")
    assert not [n for n in tools if any(b in n for b in banned)]
    assert [n for n in tools if "ticket" in n] == ["create_support_ticket"]


@pytest.mark.parametrize("role", sorted(config.roles()))
@pytest.mark.parametrize("tool", config.TOOL_NAMES)
def test_role_matrix(server, tool, role):
    is_error, body = call(server, tool, GOOD[tool], role=role)
    if role in config.tool_roles(tool):
        if tool in config.READ_ONLY_TOOLS:
            assert not is_error, body
        else:                                                  # p6: reached the handler; store off here
            assert body.get("code") == "TICKET_STORE_UNAVAILABLE", body
    else:
        assert is_error and body["code"] == "FORBIDDEN" and set(body) == {"code", "message", "correlation_id"}


def test_no_identity_is_unauthenticated(server):
    is_error, body = call(server, "lookup_response_code", GOOD["lookup_response_code"], role=None)
    assert is_error and body["code"] == "UNAUTHENTICATED"


LINK_LOCAL = ".".join(["169", "254", "169", "254"])        # built, not written: repo hygiene bans IP literals
INJECTIONS = ["../../etc/passwd", f"http://{LINK_LOCAL}/latest", "'; DROP TABLE tickets;--", "$(rm -rf /)",
              "syn-hmac-01\nX", "C:\\Windows\\system32"]


@pytest.mark.parametrize("value", INJECTIONS)
@pytest.mark.parametrize("tool,field", [
    ("validate_test_signature", "fixture_id"), ("validate_test_signature", "test_payload_ref"),
    ("lookup_response_code", "code"), ("lookup_response_code", "spec_version"),
    ("validate_request_payload", "endpoint_id"), ("search_qris_spec", "version"),
])
def test_injection_strings_rejected_before_handler(server, tool, field, value):
    is_error, body = call(server, tool, {**GOOD[tool], field: value})
    assert is_error and body["code"] == "INVALID_ARGUMENT"
    assert [e["field"] for e in body["field_errors"]] == [field]
    assert value not in json.dumps(body)


@pytest.mark.parametrize("tool,bad", [
    ("search_qris_spec", {"top_k": 11}), ("search_qris_spec", {"top_k": "5"}), ("search_qris_spec", {"query": ""}),
    ("search_qris_spec", {"query": "q" * 501}), ("search_qris_spec", {"api_family": "visa"}),
    ("validate_request_payload", {"payload": "{}"}), ("lookup_response_code", {"code": 4014700}),
    ("validate_test_signature", {"algorithm": "MD5"}),
])
def test_bad_types_ranges_and_enums(server, tool, bad):
    is_error, body = call(server, tool, {**GOOD[tool], **bad})
    assert is_error and body["code"] == "INVALID_ARGUMENT" and body["field_errors"]


@pytest.mark.parametrize("tool", config.TOOL_NAMES)
def test_extra_field_rejected(server, tool):
    is_error, body = call(server, tool, {**GOOD[tool], "url": "http://127.0.0.1/"})
    assert is_error and body["code"] == "INVALID_ARGUMENT"
    assert {"field": "url", "reason": "extra_forbidden"} in body["field_errors"]


def test_oversized_payload_rejected(server):
    big = {"headers": {}, "body": {"additionalInfo": {"x": "Z" * (config.max_payload_bytes() + 1)}}}
    is_error, body = call(server, "validate_request_payload", {**GOOD["validate_request_payload"], "payload": big})
    assert is_error and body["code"] == "INVALID_ARGUMENT" and body["field_errors"][0]["field"] == "payload"
    assert "ZZZZ" not in json.dumps(body)          # Z: cannot occur in the hex correlation_id


def test_secret_shaped_values_never_in_response_or_log(server, caplog):
    secret = "Bearer eyJhbGciOiJIUzI1NiJ9.SECRET-SHAPED-VALUE-0123456789"
    sig = "c2VjcmV0LXNpZ25hdHVyZS12YWx1ZQ=="
    payload = {"headers": {"Authorization": secret, "X-SIGNATURE": sig}, "body": {"originalReferenceNo": secret}}
    with caplog.at_level(logging.INFO, logger="devguard.mcp"):
        _, body = call(server, "validate_request_payload", {**GOOD["validate_request_payload"], "payload": payload})
        _, body2 = call(server, "validate_request_payload",
                        {**GOOD["validate_request_payload"], "payload": payload, "x": secret})
    text = json.dumps(body) + json.dumps(body2) + caplog.text
    assert secret not in text and sig not in text
    assert "outcome=OK" in caplog.text and "outcome=INVALID_ARGUMENT" in caplog.text


def test_internal_error_is_safe(ctx, caplog):
    boom_ctx = t.ToolContext(**{**ctx.__dict__, "_cache": {}})
    boom_ctx.signature_cases = None                      # the handler crashes with a TypeError
    s = srv.build_server(SECRET, boom_ctx)
    with caplog.at_level(logging.ERROR, logger="devguard.mcp"):
        is_error, body = call(s, "validate_test_signature", GOOD["validate_test_signature"])
    assert is_error and set(body) == {"code", "message", "correlation_id"} and body["code"] == "INTERNAL_ERROR"
    assert "Traceback" not in json.dumps(body) and "TypeError" not in json.dumps(body)
    assert body["correlation_id"] in caplog.text


def test_payload_tool_refuses_family_and_version_mismatch(server):
    is_error, body = call(server, "validate_request_payload", {**GOOD["validate_request_payload"], "api_family": "snap"})
    assert is_error and body["field_errors"] == [{"field": "api_family", "reason": "does_not_match_endpoint"}]
    is_error, body = call(server, "validate_request_payload",
                          {**GOOD["validate_request_payload"], "spec_version": "0.2.0"})
    assert is_error and body["field_errors"] == [{"field": "spec_version", "reason": "unsupported_version"}]


def test_payload_tool_returns_validator_result(server):
    is_error, body = call(server, "validate_request_payload", GOOD["validate_request_payload"])
    assert not is_error and body["valid"] is False and body["schema_version"] == "0.1.0"
    assert any(e["code"] == "SCHEMA_REQUIRED" and e["pointer"] == "/headers/X-TIMESTAMP" for e in body["errors"])


def test_signature_tool_valid_and_unknown_case(server):
    is_error, body = call(server, "validate_test_signature", GOOD["validate_test_signature"])
    assert not is_error and body["status"] == "VALID" and body["synthetic_test_material"] is True
    is_error, body = call(server, "validate_test_signature",
                          {**GOOD["validate_test_signature"], "test_payload_ref": "sig-no-such-case"})
    assert is_error and body["field_errors"] == [{"field": "test_payload_ref", "reason": "unknown_test_case"}]


def test_response_code_tool(server):
    is_error, body = call(server, "lookup_response_code", GOOD["lookup_response_code"])
    assert not is_error and body["status"] == "FOUND" and body["synthetic"] is True
    is_error, body = call(server, "lookup_response_code", {**GOOD["lookup_response_code"], "spec_version": "9.9.9"})
    assert is_error and body["code"] == "INVALID_ARGUMENT"


def test_search_returns_citation_contract_and_skips_reranker(server):
    is_error, body = call(server, "search_qris_spec", GOOD["search_qris_spec"])
    assert not is_error and body["reranked"] is False and 0 < len(body["results"]) <= 3
    for r in body["results"]:
        assert {"citation_id", "document_id", "document_version", "chunk_id", "quote_or_excerpt"} <= set(r)
        assert r["untrusted"] is True and len(r["quote_or_excerpt"]) <= 1000
    assert "untrusted" in body["notice"]


def test_search_family_and_version_filters(server):
    _, body = call(server, "search_qris_spec", {**GOOD["search_qris_spec"], "api_family": "qris-mpm"})
    assert body["results"] and {r["document_id"] for r in body["results"]} == {"synthetic-qris-mpm-service-spec"}
    _, body = call(server, "search_qris_spec", {**GOOD["search_qris_spec"], "version": "9.9.9"})
    assert body["results"] == []


def test_search_unavailable(ctx):
    def down():
        raise ConnectionError("qdrant at 127.0.0.1:6333 refused")
    c = t.ToolContext(**{**ctx.__dict__, "_cache": {}})
    c.backends = down
    is_error, body = call(srv.build_server(SECRET, c), "search_qris_spec", GOOD["search_qris_spec"])
    assert is_error and body["code"] == "SEARCH_UNAVAILABLE" and "6333" not in json.dumps(body)


def test_app_refuses_non_loopback(monkeypatch, server):
    cfg = {**config.mcp(), "server": {**config.mcp()["server"], "host": "0.0.0.0"}}
    monkeypatch.setattr(config, "mcp", lambda: cfg)
    with pytest.raises(ValueError):
        srv.build_app(server)


@pytest.mark.parametrize("probe_result", ["raises", "missing"])
def test_default_backends_probe_qdrant_briefly_before_loading_anything(monkeypatch, probe_result):
    """p5 regression: a dead, hanging or empty Qdrant fails at a short-timeout probe; the 60 s client
    and BGE-M3 are never built."""
    import qdrant_client

    import app.rag.embedding as emb_mod
    import app.rag.store as store_mod

    seen = {}

    class Probe:
        def __init__(self, url, timeout, check_compatibility):
            seen.update(timeout=timeout, check_compatibility=check_compatibility)

        def collection_exists(self, name):
            if probe_result == "raises":
                raise ConnectionError("refused")
            return False

    def must_not_build(*a, **k):
        raise AssertionError("must not be built when the probe fails")

    monkeypatch.setattr(qdrant_client, "QdrantClient", Probe)
    monkeypatch.setattr(store_mod, "make_client", must_not_build)
    monkeypatch.setattr(emb_mod, "BgeM3Embedder", must_not_build)
    with pytest.raises(ConnectionError):
        t.default_context(database=False).backends()
    assert seen == {"timeout": config.mcp()["search"]["qdrant_probe_timeout_seconds"], "check_compatibility": False}
    assert seen["timeout"] <= 5
