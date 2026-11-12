"""p9 API routes: thread list, pending approvals, thread history, alerts (docs/p9-demo-ui-alerts/spec.md AC #1-#5).

Database cases (`-m db`) run on the throwaway schema with the least-privilege app role, as in test_api_threads.py.
The checks that never reach the store (401, 403, 422, 503) run without PostgreSQL.
"""

from __future__ import annotations

import contextlib
import secrets

import pytest
from fastapi.testclient import TestClient

from app.agent import config
from app.agent.graph import Agent
from app.agent.harness import McpHarness
from app.agent.llm import FakeLLM
from app.agent.tools import ToolClient
from app.api.app import create_app
from app.mcp_server import auth

API_AUD = config.agent()["api"]["audience"]
SIG_CTX = {"api_family": "snap", "signature": {"algorithm": "SHA256withRSA", "fixture_id": "syn-rsa-01",
                                               "canonicalization_profile": "snap-asymmetric-access-token-v1",
                                               "test_case_ref": "sig-at-wrong-separator"}}
ESCALATE = {"message": "My access-token signature keeps failing, please create a ticket for this.", "context": SIG_CTX}
SECRET = secrets.token_hex(24)


async def up() -> bool:
    return True


def lenient_limits():
    from app.security.ratelimit import TokenBucket
    return {name: TokenBucket(10**6, 10**6) for name in ("per_address", "api_agent_run", "api_other")}


def hdr(secret, sub="dev-1", role="developer"):
    return {"Authorization": f"Bearer {auth.mint(sub, role, secret=secret, audience=API_AUD)}"}


# --- no store needed -------------------------------------------------------------------------------------------

@pytest.fixture
def bare():
    return TestClient(create_app(agent=None, secret=SECRET, mcp_ping=up, llm_ping=up, limits=lenient_limits()))


@pytest.mark.parametrize("path", ["/api/v1/threads", "/api/v1/approvals?status=pending"])
def test_new_routes_need_a_token(bare, path):
    r = bare.get(path)
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize("role", ["developer", "kb_maintainer"])
def test_pending_approvals_are_for_approver_roles_only(bare, role):
    r = bare.get("/api/v1/approvals?status=pending", headers=hdr(SECRET, role=role))
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"


@pytest.mark.parametrize("query", ["", "?status=approved", "?status=executed", "?status=PENDING"])
def test_pending_approvals_status_must_be_pending(bare, query):
    r = bare.get(f"/api/v1/approvals{query}", headers=hdr(SECRET, "boss-1", "support_engineer"))
    assert r.status_code == 422 and r.json()["code"] == "INVALID_ARGUMENT"
    assert "approved" not in r.text and "executed" not in r.text          # field names only, never values


def test_new_routes_without_a_store_are_503(bare):
    assert bare.get("/api/v1/threads", headers=hdr(SECRET)).status_code == 503
    assert bare.get("/api/v1/approvals?status=pending", headers=hdr(SECRET, "boss-1", "admin")).status_code == 503


# --- PostgreSQL --------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def harness(migrated_db_url, db_roles):
    with McpHarness(db_conninfo=migrated_db_url, audit_conninfo=db_roles.url("audit"), rate_limits=False) as h:
        yield h


@pytest.fixture(scope="module")
def client(harness, db_roles):
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    app_role = db_roles.url("app")

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with AsyncPostgresSaver.from_conn_string(app_role) as saver:
            app.state.agent = Agent(ToolClient(harness.url, harness.secret), FakeLLM(), checkpointer=saver,
                                    db_conninfo=app_role)
            yield

    app = create_app(agent=None, secret=harness.secret, mcp_ping=up, llm_ping=up, db_conninfo=app_role,
                     lifespan=lifespan, limits=lenient_limits())
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def new_thread(client, harness, sub):
    r = client.post("/api/v1/threads", headers=hdr(harness.secret, sub))
    assert r.status_code == 201, r.text
    return r.json()["thread_id"]


@pytest.mark.db
def test_thread_list_is_owner_scoped_and_newest_first(client, harness):
    me, other = f"p9-{secrets.token_hex(4)}", f"p9-{secrets.token_hex(4)}"
    mine = [new_thread(client, harness, me) for _ in range(3)]
    theirs = new_thread(client, harness, other)
    r = client.post(f"/api/v1/threads/{mine[0]}/messages", json={"message": "what does 4017300 mean?"},
                    headers=hdr(harness.secret, me))
    assert r.status_code == 200, r.text                   # touches updated_at of the oldest thread
    body = client.get("/api/v1/threads", headers=hdr(harness.secret, me)).json()
    ids = [t["thread_id"] for t in body["threads"]]
    assert set(ids) == set(mine) and theirs not in ids
    assert ids[0] == mine[0]                               # most recently updated first
    assert set(body["threads"][0]) == {"thread_id", "status", "created_at", "updated_at"}
    assert body["threads"][0]["status"] == "completed"


@pytest.mark.db
def test_thread_list_is_capped(client, harness):
    me = f"p9-{secrets.token_hex(4)}"
    from app.db import store
    with store.connect(harness.db_conninfo) as conn:      # owner role: seeding only
        for _ in range(55):
            store.create_thread(conn, secrets.token_urlsafe(24), me)
    assert len(client.get("/api/v1/threads", headers=hdr(harness.secret, me)).json()["threads"]) == 50


@pytest.mark.db
def test_pending_approvals_list_for_an_approver(client, harness):
    dev = f"p9-{secrets.token_hex(4)}"
    tid = new_thread(client, harness, dev)
    r = client.post(f"/api/v1/threads/{tid}/messages", json=ESCALATE, headers=hdr(harness.secret, dev))
    assert r.status_code == 200 and r.json()["status"] == "awaiting_approval", r.text
    approval_id = r.json()["approval"]["approval_id"]
    for role in ("support_engineer", "admin"):
        got = client.get("/api/v1/approvals?status=pending", headers=hdr(harness.secret, "boss-1", role))
        assert got.status_code == 200
        rows = got.json()["approvals"]
        assert all(a["status"] == "pending" for a in rows)
        mine = [a for a in rows if a["approval_id"] == approval_id]
        assert len(mine) == 1 and mine[0]["thread_id"] == tid and mine[0]["requester_id"] == dev
        assert mine[0]["action"]["target"] == "local sandbox_tickets table"
    # decided -> no longer listed
    d = client.post(f"/api/v1/approvals/{approval_id}/decision", json={"decision": "reject"},
                    headers=hdr(harness.secret, "boss-1", "support_engineer"))
    assert d.status_code == 200, d.text
    rows = client.get("/api/v1/approvals?status=pending", headers=hdr(harness.secret, "boss-1", "admin")).json()
    assert approval_id not in [a["approval_id"] for a in rows["approvals"]]


# --- thread history (AC #2) --------------------------------------------------------------------------------------

PLANTED = "S3cr3tValueSYN"


@pytest.fixture(scope="module")
def mem_harness():
    with McpHarness(rate_limits=False) as h:
        yield h


def test_history_in_memory_lists_completed_runs_in_order_with_redaction(mem_harness):
    import asyncio
    agent = Agent(ToolClient(mem_harness.url, mem_harness.secret), FakeLLM())

    async def go():
        a = await agent.run(sub="dev-1", role="developer", message="what does 4017300 mean?", hitl=False)
        tid = a["thread_id"]
        b = await agent.run(sub="dev-1", role="developer", thread_id=tid, hitl=False,
                            message=f'client_secret: "{PLANTED}" why is my signature rejected?', context=SIG_CTX)
        return tid, a, b, await agent.history(tid)

    tid, a, b, runs = asyncio.run(go())
    assert [r["run_id"] for r in runs] == [a["run_id"], b["run_id"]]
    assert runs[0]["answer"] == a["answer"] and runs[1]["answer"] == b["answer"]     # not the previous run's answer
    assert "4017300" in runs[0]["question"]
    assert PLANTED not in str(runs) and "why is my signature rejected?" in runs[1]["question"]
    assert all(r["at"] for r in runs)


def test_history_of_an_unknown_thread_is_empty(mem_harness):
    import asyncio
    agent = Agent(ToolClient(mem_harness.url, mem_harness.secret), FakeLLM())
    assert asyncio.run(agent.history(secrets.token_urlsafe(24))) == []


@pytest.mark.db
def test_history_route_on_postgres_skips_a_run_awaiting_approval(client, harness):
    dev = f"p9-{secrets.token_hex(4)}"
    tid = new_thread(client, harness, dev)
    first = client.post(f"/api/v1/threads/{tid}/messages", json={"message": f'password: "{PLANTED}" what does 4017300 mean?'},
                        headers=hdr(harness.secret, dev)).json()
    waiting = client.post(f"/api/v1/threads/{tid}/messages", json=ESCALATE, headers=hdr(harness.secret, dev)).json()
    assert waiting["status"] == "awaiting_approval"
    got = client.get(f"/api/v1/threads/{tid}/messages", headers=hdr(harness.secret, dev))
    assert got.status_code == 200
    runs = got.json()["runs"]
    assert [r["run_id"] for r in runs] == [first["run_id"]] and PLANTED not in got.text
    # approved and resumed -> the escalated run is listed too, with its own answer
    d = client.post(f"/api/v1/approvals/{waiting['approval']['approval_id']}/decision", json={"decision": "approve"},
                    headers=hdr(harness.secret, "boss-1", "support_engineer"))
    assert d.status_code == 200, d.text
    runs = client.get(f"/api/v1/threads/{tid}/messages", headers=hdr(harness.secret, dev)).json()["runs"]
    assert [r["run_id"] for r in runs] == [first["run_id"], waiting["run_id"]]
    assert runs[1]["answer"] != runs[0]["answer"]


@pytest.mark.db
def test_history_of_someone_elses_or_unknown_thread_is_the_same_404(client, harness):
    owner = f"p9-{secrets.token_hex(4)}"
    tid = new_thread(client, harness, owner)
    other = client.get(f"/api/v1/threads/{tid}/messages", headers=hdr(harness.secret, "p9-intruder"))
    unknown = client.get(f"/api/v1/threads/{secrets.token_urlsafe(24)}/messages", headers=hdr(harness.secret, owner))
    assert other.status_code == unknown.status_code == 404
    assert other.json().keys() == unknown.json().keys() and other.json()["code"] == unknown.json()["code"] == "NOT_FOUND"
