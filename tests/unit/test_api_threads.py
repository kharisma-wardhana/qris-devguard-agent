"""Thread and approval routes with the PostgreSQL checkpointer (p6 spec AC #2, #4, #5, #6, #14)."""

from __future__ import annotations

import contextlib
import secrets
import uuid

import pytest
from fastapi.testclient import TestClient

from app.agent import config
from app.agent.graph import Agent
from app.agent.harness import McpHarness
from app.agent.llm import FakeLLM
from app.agent.tools import ToolClient
from app.api.app import create_app
from app.db import store
from app.mcp_server import auth

API_AUD = config.agent()["api"]["audience"]
SIG_CTX = {"api_family": "snap", "signature": {"algorithm": "SHA256withRSA", "fixture_id": "syn-rsa-01",
                                               "canonicalization_profile": "snap-asymmetric-access-token-v1",
                                               "test_case_ref": "sig-at-wrong-separator"}}
ESCALATE = {"message": "My access-token signature keeps failing, please create a ticket for this.", "context": SIG_CTX}


async def up() -> bool:
    return True


@pytest.fixture(scope="module")
def harness(migrated_db_url, db_roles):
    # p7: the write tool fails closed without an audit trail, so the harness gets the throwaway audit role
    with McpHarness(db_conninfo=migrated_db_url, audit_conninfo=db_roles.url("audit")) as h:
        yield h


@pytest.fixture(scope="module")
def client(harness, migrated_db_url):
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with AsyncPostgresSaver.from_conn_string(migrated_db_url) as saver:
            app.state.agent = Agent(ToolClient(harness.url, harness.secret), FakeLLM(), checkpointer=saver,
                                    db_conninfo=migrated_db_url)
            yield

    app = create_app(agent=None, secret=harness.secret, mcp_ping=up, llm_ping=up, db_conninfo=migrated_db_url,
                     lifespan=lifespan)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def hdr(harness, sub="dev-1", role="developer"):
    return {"Authorization": f"Bearer {auth.mint(sub, role, secret=harness.secret, audience=API_AUD)}"}


def new_thread(client, harness, sub="dev-1", role="developer"):
    r = client.post("/api/v1/threads", headers=hdr(harness, sub, role))
    assert r.status_code == 201, r.text
    return r.json()["thread_id"]


def escalated(client, harness, sub="dev-1"):
    tid = new_thread(client, harness, sub)
    r = client.post(f"/api/v1/threads/{tid}/messages", json=ESCALATE, headers=hdr(harness, sub))
    assert r.status_code == 200, r.text
    return tid, r.json()


def tickets(url, tid):
    with store.connect(url) as c:
        return c.execute("select count(*) as n from sandbox_tickets where thread_id = %s", (tid,)).fetchone()["n"]


@pytest.mark.db
def test_thread_create_message_read(client, harness):
    tid = new_thread(client, harness)
    r = client.post(f"/api/v1/threads/{tid}/messages", json={"message": "why is my signature rejected?", "context": SIG_CTX},
                    headers=hdr(harness))
    body = r.json()
    assert r.status_code == 200 and body["status"] == "completed" and body["thread_id"] == tid and body["approval"] is None
    got = client.get(f"/api/v1/threads/{tid}", headers=hdr(harness)).json()
    assert got["status"] == "completed" and got["run_id"] == body["run_id"] and got["answer"]["summary"]


@pytest.mark.db
def test_other_owner_and_unknown_threads_are_404(client, harness):
    tid = new_thread(client, harness)
    for path, method in ((f"/api/v1/threads/{tid}", "get"), (f"/api/v1/threads/{tid}/messages", "post"),
                         (f"/api/v1/threads/{secrets.token_urlsafe(24)}", "get")):
        kw = {"json": {"message": "q"}} if method == "post" else {}
        r = getattr(client, method)(path, headers=hdr(harness, "dev-2"), **kw)
        assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"


@pytest.mark.db
def test_escalation_waits_for_approval_and_view_is_complete(client, harness):
    tid, body = escalated(client, harness)
    assert body["status"] == "awaiting_approval" and body["approval"]["status"] == "pending"
    aid = body["approval"]["approval_id"]
    view = client.get(f"/api/v1/approvals/{aid}", headers=hdr(harness, "sup-1", "support_engineer")).json()
    assert view["action"]["type"] == "create_support_ticket" and view["action"]["target"] == "local sandbox_tickets table"
    assert view["expected_side_effect"].startswith("one row") and view["requester_id"] == "dev-1"
    assert view["evidence_ids"] and view["expires_at"] and view["status"] == "pending"
    assert client.get(f"/api/v1/approvals/{aid}", headers=hdr(harness)).status_code == 200          # requester
    assert client.get(f"/api/v1/approvals/{aid}", headers=hdr(harness, "dev-9")).status_code == 404  # stranger
    assert client.post(f"/api/v1/threads/{tid}/messages", json={"message": "q"}, headers=hdr(harness)).status_code == 409


@pytest.mark.db
@pytest.mark.parametrize("sub,role,status", [("dev-7", "developer", 403), ("kb-1", "kb_maintainer", 403),
                                             ("dev-1", "developer", 403)])          # incl. the requester
def test_non_approver_roles_cannot_decide(client, harness, sub, role, status):
    _, body = escalated(client, harness)
    r = client.post(f"/api/v1/approvals/{body['approval']['approval_id']}/decision", json={"decision": "approve"},
                    headers=hdr(harness, sub, role))
    assert r.status_code == status and r.json()["code"] == "FORBIDDEN"


@pytest.mark.db
def test_requester_cannot_self_approve_even_as_admin(client, harness):
    tid = new_thread(client, harness, "adm-1", "admin")
    r = client.post(f"/api/v1/threads/{tid}/messages", json=ESCALATE, headers=hdr(harness, "adm-1", "admin"))
    aid = r.json()["approval"]["approval_id"]
    r = client.post(f"/api/v1/approvals/{aid}/decision", json={"decision": "approve"}, headers=hdr(harness, "adm-1", "admin"))
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"


@pytest.mark.db
def test_approve_creates_one_ticket_and_second_decision_conflicts(client, harness, migrated_db_url):
    tid, body = escalated(client, harness)
    aid = body["approval"]["approval_id"]
    r = client.post(f"/api/v1/approvals/{aid}/decision", json={"decision": "approve", "reason": "matches the fixture"},
                    headers=hdr(harness, "sup-1", "support_engineer"))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["approval"]["status"] == "executed" and out["run"]["status"] == "completed"
    assert out["run"]["answer"]["action"]["status"] == "created" and out["approval"]["ticket_id"]
    assert tickets(migrated_db_url, tid) == 1
    again = client.post(f"/api/v1/approvals/{aid}/decision", json={"decision": "approve"},
                        headers=hdr(harness, "sup-2", "support_engineer"))
    assert again.status_code == 409 and tickets(migrated_db_url, tid) == 1
    assert client.post(f"/api/v1/threads/{tid}/resume", headers=hdr(harness)).status_code == 409
    assert client.get(f"/api/v1/threads/{tid}", headers=hdr(harness)).json()["status"] == "completed"


@pytest.mark.db
def test_reject(client, harness, migrated_db_url):
    tid, body = escalated(client, harness)
    r = client.post(f"/api/v1/approvals/{body['approval']['approval_id']}/decision", json={"decision": "reject"},
                    headers=hdr(harness, "sup-1", "support_engineer"))
    assert r.status_code == 200 and r.json()["run"]["answer"]["action"]["status"] == "rejected"
    assert tickets(migrated_db_url, tid) == 0


@pytest.mark.db
def test_expired_decision_is_409_and_creates_nothing(client, harness, migrated_db_url):
    tid, body = escalated(client, harness)
    aid = body["approval"]["approval_id"]
    with store.connect(migrated_db_url) as c, c.transaction():
        c.execute("update approval_requests set expires_at = now() - interval '1 second' where approval_id = %s", (aid,))
    r = client.post(f"/api/v1/approvals/{aid}/decision", json={"decision": "approve"},
                    headers=hdr(harness, "sup-1", "support_engineer"))
    assert r.status_code == 409 and r.json()["code"] == "APPROVAL_EXPIRED" and tickets(migrated_db_url, tid) == 0
    assert client.get(f"/api/v1/threads/{tid}", headers=hdr(harness)).json()["action_result"]["status"] == "expired"


@pytest.mark.db
def test_resume_after_a_decision_that_never_resumed(client, harness, migrated_db_url):
    tid, body = escalated(client, harness)
    aid = body["approval"]["approval_id"]
    assert client.post(f"/api/v1/threads/{tid}/resume", headers=hdr(harness)).status_code == 409   # still pending
    with store.connect(migrated_db_url) as c:
        store.decide(c, aid, "sup-1", "approve")                                                  # recorded, not resumed
    r = client.post(f"/api/v1/threads/{tid}/resume", headers=hdr(harness))
    assert r.status_code == 200 and r.json()["answer"]["action"]["status"] == "created" and tickets(migrated_db_url, tid) == 1


@pytest.mark.db
def test_chat_stays_one_shot_and_never_creates_an_approval(client, harness, migrated_db_url):
    r = client.post("/api/v1/chat", json=ESCALATE, headers=hdr(harness))
    body = r.json()
    assert r.status_code == 200 and set(body) == {"run_id", "thread_id", "intent", "risk_level", "answer"}
    assert body["answer"]["draft"]["status"] == "draft_only"
    with store.connect(migrated_db_url) as c:
        n = c.execute("select count(*) as n from approval_requests where thread_id = %s", (body["thread_id"],)).fetchone()["n"]
    assert n == 0


def test_routes_without_a_store_are_503(harness):
    app = create_app(agent=None, secret=harness.secret, mcp_ping=up, llm_ping=up)
    r = TestClient(app).post("/api/v1/threads", headers=hdr(harness))
    assert r.status_code == 503 and r.json()["code"] == "SERVICE_UNAVAILABLE"


def test_malformed_ids_are_422(harness):
    app = create_app(agent=None, secret=harness.secret, mcp_ping=up, llm_ping=up)
    c = TestClient(app)
    assert c.get("/api/v1/threads/../etc", headers=hdr(harness)).status_code in (404, 422)
    assert c.get(f"/api/v1/approvals/{uuid.uuid4().hex}", headers=hdr(harness)).status_code == 422
