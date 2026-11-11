"""Audit writer and the guarded tool's audit behaviour (p7 spec Behaviour §1, AC #1, #3, #4)."""

from __future__ import annotations

import asyncio
import json
import secrets
import uuid

import psycopg
import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken

from app.db import actions, audit, store
from app.mcp_server import errors
from app.mcp_server import server as srv
from app.mcp_server import tools as t

PAYLOAD = {"title": "Audit fail-closed check", "body": "SYN: audit unavailable case",
           "evidence_ids": ["CIT-000001"], "priority": "low"}
LOOKUP = {"code": "4094700", "api_family": "qris-mpm", "spec_version": "0.1.0"}


def call(server, name, args, sub="dev-1", role="developer"):
    async def go():
        auth_context_var.set(AuthenticatedUser(AccessToken(token="t", client_id=sub, scopes=[role], subject=sub,
                                                           claims={"role": role})))
        return await server.call_tool(name, args)
    res = asyncio.run(go())
    return res.is_error, json.loads(res.content[0].text)


def broken_writer() -> audit.AuditWriter:
    def connect():
        raise psycopg.OperationalError("down")
    return audit.AuditWriter(connect)


# --- unit (no database) -------------------------------------------------------------------------------

def test_every_mcp_error_code_is_an_audit_outcome():
    assert set(errors.MESSAGES) <= audit.OUTCOMES
    assert {"OK", "ATTEMPT", "PAYLOAD_TOO_LARGE"} <= audit.OUTCOMES


def test_writer_returns_false_when_database_is_down():
    assert broken_writer().write(correlation_id="a" * 32, sub="dev-1", role="developer", tool="x",
                                 phase="result", outcome="OK") is False


def test_writer_refuses_unknown_outcome_or_phase_locally():
    w = broken_writer()
    assert w.write(correlation_id="a" * 32, sub="s", role="developer", tool="x", phase="result",
                   outcome="client_secret=abc") is False
    assert w.write(correlation_id="a" * 32, sub="s", role="developer", tool="x", phase="later", outcome="OK") is False


@pytest.mark.parametrize("value,ok", [(str(uuid.uuid4()), True), ("not-a-uuid", False), (None, False),
                                      ("'; DROP TABLE tool_audit_logs; --", False),
                                      (str(uuid.uuid4()).upper(), False)])
def test_valid_uuid(value, ok):
    assert audit.valid_uuid(value) == (value if ok else None)


def test_read_tool_still_answers_when_audit_fails():
    ctx = t.default_context(database=False)
    ctx.audit = broken_writer()
    err, body = call(srv.build_server("s" * 48, ctx), "lookup_response_code", LOOKUP)
    assert not err and body["status"] == "FOUND"


def test_write_tool_without_audit_writer_fails_closed():
    ctx = t.default_context(database=False)
    ctx.db = lambda: (_ for _ in ()).throw(AssertionError("the ticket store must not be touched"))
    ctx.audit = None
    err, body = call(srv.build_server("s" * 48, ctx), "create_support_ticket",
                     {"approval_id": str(uuid.uuid4()), "thread_id": "t" * 32, **PAYLOAD,
                      "idempotency_key": "0" * 64})
    assert err and body["code"] == "AUDIT_UNAVAILABLE" and set(body) == {"code", "message", "correlation_id"}


def test_purge_refuses_zero_days():
    with pytest.raises(ValueError):
        audit.purge(None, 0)


# --- database ----------------------------------------------------------------------------------------

@pytest.fixture
def owner(migrated_db_url):
    with psycopg.connect(migrated_db_url, autocommit=True) as c:
        yield c


@pytest.fixture
def ctx(migrated_db_url, db_roles):
    c = t.default_context(database=False)
    c.db = lambda: store.connect(migrated_db_url)
    c.audit = audit.AuditWriter(audit.connector(db_roles.url("audit")))
    return c


@pytest.mark.db
def test_write_tool_audit_down_creates_nothing(migrated_db_url, owner):
    ctx = t.default_context(database=False)
    ctx.db = lambda: store.connect(migrated_db_url)
    ctx.audit = broken_writer()
    with store.connect(migrated_db_url) as c:
        tid = secrets.token_urlsafe(24)
        store.create_thread(c, tid, "dev-1")
        a = store.get_or_create_pending(c, thread_id=tid, run_id=str(uuid.uuid4()), requester_id="dev-1",
                                        requester_role="developer", payload=PAYLOAD, ttl_minutes=30)
        store.decide(c, str(a["approval_id"]), "sup-1", "approve")
    err, body = call(srv.build_server("s" * 48, ctx), "create_support_ticket",
                     {"approval_id": str(a["approval_id"]), "thread_id": tid, **PAYLOAD,
                      "idempotency_key": actions.idempotency_key(tid, PAYLOAD)})
    assert err and body["code"] == "AUDIT_UNAVAILABLE"
    assert owner.execute("select count(*) from sandbox_tickets where thread_id = %s", (tid,)).fetchone()[0] == 0
    assert owner.execute("select status from approval_requests where approval_id = %s",
                         (a["approval_id"],)).fetchone()[0] == "approved"


@pytest.mark.db
@pytest.mark.parametrize("name,args,role,outcome", [
    ("lookup_response_code", LOOKUP, "developer", "OK"),
    ("validate_test_signature", {"algorithm": "HMAC_SHA512", "fixture_id": "syn-hmac-01",
                                 "canonicalization_profile": "synthetic-snap-symmetric-v1",
                                 "test_payload_ref": "sig-gen-body-not-minified"}, "kb_maintainer", "FORBIDDEN"),
    ("lookup_response_code", {**LOOKUP, "code": "../../etc/passwd"}, "developer", "INVALID_ARGUMENT"),
])
def test_one_result_row_per_call_matching_the_response(ctx, owner, name, args, role, outcome):
    err, body = call(srv.build_server("s" * 48, ctx), name, args, sub="aud-1", role=role)
    rows = audit.rows(owner, correlation_id=body["correlation_id"])
    assert len(rows) == 1
    r = rows[0]
    assert (r["actor_sub"], r["actor_role"], r["tool"], r["phase"], r["outcome"]) == ("aud-1", role, name, "result",
                                                                                      outcome)
    assert r["duration_ms"] >= 0 and r["run_id"] is None


@pytest.mark.db
def test_internal_error_is_audited(ctx, owner):
    boom = t.ToolContext(**{**ctx.__dict__, "_cache": {}})
    boom.signature_cases = None                                 # the handler crashes with a TypeError (P4 trick)
    err, body = call(srv.build_server("s" * 48, boom), "validate_test_signature",
                     {"algorithm": "HMAC_SHA512", "fixture_id": "syn-hmac-01",
                      "canonicalization_profile": "synthetic-snap-symmetric-v1",
                      "test_payload_ref": "sig-gen-body-not-minified"}, sub="aud-2")
    assert err and body["code"] == "INTERNAL_ERROR"
    (row,) = audit.rows(owner, correlation_id=body["correlation_id"])
    assert row["outcome"] == "INTERNAL_ERROR" and row["tool"] == "validate_test_signature"


@pytest.mark.db
def test_write_tool_writes_attempt_then_result(ctx, owner, migrated_db_url):
    with store.connect(migrated_db_url) as c:
        tid = secrets.token_urlsafe(24)
        store.create_thread(c, tid, "dev-1")
        a = store.get_or_create_pending(c, thread_id=tid, run_id=str(uuid.uuid4()), requester_id="dev-1",
                                        requester_role="developer", payload=PAYLOAD, ttl_minutes=30)
        store.decide(c, str(a["approval_id"]), "sup-1", "approve")
    err, body = call(srv.build_server("s" * 48, ctx), "create_support_ticket",
                     {"approval_id": str(a["approval_id"]), "thread_id": tid, **PAYLOAD,
                      "idempotency_key": actions.idempotency_key(tid, PAYLOAD)})
    assert not err and body["status"] == "created"
    rows = audit.rows(owner, correlation_id=body["correlation_id"])
    assert [(r["phase"], r["outcome"]) for r in rows] == [("attempt", "ATTEMPT"), ("result", "OK")]
    assert all(str(r["approval_id"]) == str(a["approval_id"]) for r in rows)


@pytest.mark.db
def test_purge_deletes_only_old_rows(owner, db_roles):
    w = audit.AuditWriter(audit.connector(db_roles.url("audit")))
    cid_new, cid_old = secrets.token_hex(16), secrets.token_hex(16)
    for cid in (cid_new, cid_old):
        assert w.write(correlation_id=cid, sub="purge-1", role="developer", tool="lookup_response_code",
                       phase="result", outcome="OK")
    owner.execute("update tool_audit_logs set occurred_at = now() - interval '91 days' where correlation_id = %s",
                  (cid_old,))
    assert audit.purge(owner, 90) >= 1
    assert audit.rows(owner, correlation_id=cid_old) == [] and len(audit.rows(owner, correlation_id=cid_new)) == 1
