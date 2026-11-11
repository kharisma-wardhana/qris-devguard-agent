"""create_support_ticket through the guarded MCP tool against a migrated test schema (p6 spec AC #7, #8)."""

from __future__ import annotations

import asyncio
import json
import secrets
import uuid

import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken

from app.db import actions, store
from app.mcp_server import server as srv
from app.mcp_server import tools as t

PAYLOAD = {"title": "Access-token signature rejected", "body": "SYN: sig-at-wrong-separator, INVALID",
           "evidence_ids": ["CIT-000007", "VAL-0"], "priority": "medium"}


@pytest.fixture(scope="module")
def server(migrated_db_url, db_roles):
    from app.db.audit import AuditWriter, connector
    ctx = t.default_context(database=False)
    ctx.db = lambda: store.connect(migrated_db_url)
    ctx.audit = AuditWriter(connector(db_roles.url("audit")))      # p7: the write tool fails closed without one
    return srv.build_server("s" * 48, ctx)


@pytest.fixture
def conn(migrated_db_url):
    with store.connect(migrated_db_url) as c:
        yield c


def call(server, args, sub="dev-1", role="developer"):
    async def go():
        auth_context_var.set(AuthenticatedUser(AccessToken(token="t", client_id=sub, scopes=[role], subject=sub,
                                                           claims={"role": role})))
        return await server.call_tool("create_support_ticket", args)
    res = asyncio.run(go())
    return res.is_error, json.loads(res.content[0].text)


def setup(conn, *, owner="dev-1", decide="approve", payload=PAYLOAD):
    tid = secrets.token_urlsafe(24)
    store.create_thread(conn, tid, owner)
    a = store.get_or_create_pending(conn, thread_id=tid, run_id=str(uuid.uuid4()), requester_id=owner,
                                    requester_role="developer", payload=payload, ttl_minutes=30)
    if decide:
        store.decide(conn, str(a["approval_id"]), "sup-1", decide)
    return tid, str(a["approval_id"])


def args(tid, aid, payload=PAYLOAD, **over):
    return {"approval_id": aid, "thread_id": tid, **payload,
            "idempotency_key": actions.idempotency_key(tid, payload), **over}


def tickets(conn, tid):
    return conn.execute("select count(*) as n from sandbox_tickets where thread_id = %s", (tid,)).fetchone()["n"]


@pytest.mark.db
def test_creates_exactly_one_ticket_and_marks_approval_executed(server, conn):
    tid, aid = setup(conn)
    err, body = call(server, args(tid, aid))
    assert not err and body["status"] == "created" and body["target"] == "local sandbox_tickets table"
    appr = store.get_approval(conn, aid)
    assert appr["status"] == "executed" and str(appr["ticket_id"]) == body["ticket_id"] and tickets(conn, tid) == 1


@pytest.mark.db
def test_replay_returns_the_same_ticket(server, conn):
    tid, aid = setup(conn)
    _, first = call(server, args(tid, aid))
    err, again = call(server, args(tid, aid))
    assert not err and again["status"] == "already_created" and again["ticket_id"] == first["ticket_id"]
    assert tickets(conn, tid) == 1


@pytest.mark.db
@pytest.mark.parametrize("field,value", [("title", "Different title"), ("body", "changed"),
                                         ("evidence_ids", ["CIT-000001"]), ("priority", "high")])
def test_changed_payload_is_a_mismatch(server, conn, field, value):
    tid, aid = setup(conn)
    changed = {**PAYLOAD, field: value}
    err, body = call(server, args(tid, aid, changed))
    assert err and body["code"] == "APPROVAL_MISMATCH" and tickets(conn, tid) == 0


@pytest.mark.db
def test_other_user_or_thread_is_invalid(server, conn):
    tid, aid = setup(conn)
    err, body = call(server, args(tid, aid), sub="dev-2")
    assert err and body["code"] == "APPROVAL_INVALID"
    other = secrets.token_urlsafe(24)
    store.create_thread(conn, other, "dev-1")
    err, body = call(server, args(other, aid))
    assert err and body["code"] == "APPROVAL_INVALID" and tickets(conn, tid) == tickets(conn, other) == 0


@pytest.mark.db
def test_unknown_approval_is_invalid(server, conn):
    tid, _ = setup(conn)
    err, body = call(server, args(tid, str(uuid.uuid4())))
    assert err and body["code"] == "APPROVAL_INVALID"


@pytest.mark.db
@pytest.mark.parametrize("decision", [None, "reject"])
def test_pending_or_rejected_is_not_usable(server, conn, decision):
    tid, aid = setup(conn, decide=decision)
    err, body = call(server, args(tid, aid))
    assert err and body["code"] == "APPROVAL_NOT_USABLE" and tickets(conn, tid) == 0


@pytest.mark.db
def test_expired_approval(server, conn):
    tid, aid = setup(conn)
    with conn.transaction():
        conn.execute("update approval_requests set expires_at = now() - interval '1 second' where approval_id = %s",
                     (aid,))
    err, body = call(server, args(tid, aid))
    assert err and body["code"] == "APPROVAL_EXPIRED" and tickets(conn, tid) == 0
    assert store.get_approval(conn, aid)["status"] == "expired"


@pytest.mark.db
def test_wrong_idempotency_key_is_invalid_argument(server, conn):
    tid, aid = setup(conn)
    err, body = call(server, args(tid, aid, idempotency_key="0" * 64))
    assert err and body["code"] == "INVALID_ARGUMENT" and body["field_errors"] == [
        {"field": "idempotency_key", "reason": "does_not_match_payload"}]
    assert tickets(conn, tid) == 0


@pytest.mark.db
def test_secret_in_text_is_refused_not_rewritten(server, conn):
    payload = {**PAYLOAD, "body": "client_secret=SuperSecretSYN123"}
    tid, aid = setup(conn, payload=payload)
    err, body = call(server, args(tid, aid, payload))
    assert err and body["field_errors"] == [{"field": "body", "reason": "contains_secret"}]
    assert "SuperSecretSYN123" not in json.dumps(body) and tickets(conn, tid) == 0


def test_store_unavailable():
    ctx = t.default_context(database=False)
    ctx.db = None
    s = srv.build_server("s" * 48, ctx)
    err, body = call(s, args("t" * 32, str(uuid.uuid4())))
    assert err and body["code"] == "TICKET_STORE_UNAVAILABLE"


def test_kb_maintainer_is_forbidden():
    ctx = t.default_context(database=False)
    ctx.db = None
    err, body = call(srv.build_server("s" * 48, ctx), args("t" * 32, str(uuid.uuid4())), role="kb_maintainer")
    assert err and body["code"] == "FORBIDDEN"
