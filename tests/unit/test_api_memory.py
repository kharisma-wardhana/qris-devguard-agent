"""Memory and feedback routes in-process on PostgreSQL as devguard_app (p8 spec AC #1, #2, #4, #6, #7, #8, #10, #19).

No agent is needed: threads are seeded by the owner. Auth uses real API-audience tokens.
"""

from __future__ import annotations

import secrets
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.agent import config
from app.api.app import create_app
from app.mcp_server import auth

pytestmark = pytest.mark.db

API_AUD = config.agent()["api"]["audience"]
SECRET = "m" * 48


async def up() -> bool:
    return True


class Recorder:
    def __init__(self):
        self.scores = []

    def score_run(self, run_id, name, value, **kw):
        self.scores.append((run_id, name, value, kw))


@pytest.fixture(scope="module")
def client(db_roles):
    from tests.unit.test_api_threads import lenient_limits

    app = create_app(agent=None, secret=SECRET, mcp_ping=up, llm_ping=up, db_conninfo=db_roles.url("app"),
                     limits=lenient_limits())
    app.state.tracer = Recorder()
    with TestClient(app, raise_server_exceptions=False) as c:
        c.recorder = app.state.tracer
        yield c


def hdr(sub, role="developer"):
    return {"Authorization": f"Bearer {auth.mint(sub, role, secret=SECRET, audience=API_AUD)}"}


def user() -> str:
    return "u-" + secrets.token_hex(4)


def thread(migrated_db_url, owner, run_id=True) -> str:
    tid = "mem" + secrets.token_hex(10)
    with psycopg.connect(migrated_db_url, autocommit=True) as c:
        c.execute("INSERT INTO agent_threads (thread_id, owner_id, status, last_run_id) VALUES (%s, %s, 'completed', %s)",
                  (tid, owner, str(uuid.uuid4()) if run_id else None))
    return tid


def envelope_ok(r, status, code):
    body = r.json()
    assert r.status_code == status, r.text
    assert body["code"] == code and set(body) <= {"code", "message", "correlation_id", "field_errors"}


# --- preferences ---------------------------------------------------------------------------------

def test_preference_save_update_unchanged_list_delete(client):
    u = user()
    r = client.put("/api/v1/memory/preferences/language", json={"value": "id"}, headers=hdr(u))
    assert r.status_code == 200 and r.json()["version"] == 1 and r.json()["memory_decision"] == "saved_preference"
    r = client.put("/api/v1/memory/preferences/language", json={"value": "en"}, headers=hdr(u))
    assert r.json()["version"] == 2 and r.json()["memory_decision"] == "updated_preference"
    r = client.put("/api/v1/memory/preferences/language", json={"value": "en"}, headers=hdr(u))
    assert r.json()["version"] == 2 and r.json()["memory_decision"] == "unchanged_preference"
    listed = client.get("/api/v1/memory/preferences", headers=hdr(u)).json()["preferences"]
    assert [(p["key"], p["value"]) for p in listed] == [("language", "en")]
    assert client.delete("/api/v1/memory/preferences/language", headers=hdr(u)).status_code == 204
    assert client.get("/api/v1/memory/preferences", headers=hdr(u)).json()["preferences"] == []


@pytest.mark.parametrize("key,body", [
    ("language", {"value": "fr"}),
    ("tone", {"value": "formal"}),
    ("language", {"value": "id", "extra": 1}),
    ("language", {"value": 5}),
    ("Language", {"value": "id"}),
])
def test_disallowed_preference_is_422_and_writes_nothing(client, key, body):
    u = user()
    r = client.put(f"/api/v1/memory/preferences/{key}", json=body, headers=hdr(u))
    envelope_ok(r, 422, "INVALID_ARGUMENT")
    assert client.get("/api/v1/memory/preferences", headers=hdr(u)).json()["preferences"] == []


def test_preferences_are_isolated(client):
    a, b = user(), user()
    client.put("/api/v1/memory/preferences/detail", json={"value": "brief"}, headers=hdr(a))
    assert client.get("/api/v1/memory/preferences", headers=hdr(b)).json()["preferences"] == []
    envelope_ok(client.delete("/api/v1/memory/preferences/detail", headers=hdr(b)), 404, "NOT_FOUND")
    assert len(client.get("/api/v1/memory/preferences", headers=hdr(a)).json()["preferences"]) == 1


def test_routes_need_a_token(client):
    for method, path in [("get", "/api/v1/memory/preferences"), ("get", "/api/v1/memory/patterns"),
                         ("delete", "/api/v1/memory/preferences/language")]:
        envelope_ok(getattr(client, method)(path), 401, "UNAUTHENTICATED")


# --- patterns ------------------------------------------------------------------------------------

def proposal(tid, **kw):
    return {"thread_id": tid, "title": "Timestamp offset", "text": "Use +07:00 in X-TIMESTAMP.", "intent": "timestamp", **kw}


def test_pattern_propose_review_list_delete(client, migrated_db_url):
    p, r = user(), user()
    tid = thread(migrated_db_url, p)
    created = client.post("/api/v1/memory/patterns", json=proposal(tid, api_family="qris-mpm"), headers=hdr(p))
    assert created.status_code == 201, created.text
    pid = created.json()["pattern_id"]
    assert created.json()["status"] == "pending"
    other = user()
    assert pid not in [x["pattern_id"] for x in client.get("/api/v1/memory/patterns", headers=hdr(other)).json()["patterns"]]
    assert pid in [x["pattern_id"] for x in client.get("/api/v1/memory/patterns", headers=hdr(r, "support_engineer")).json()["patterns"]]
    d = client.post(f"/api/v1/memory/patterns/{pid}/decision", json={"decision": "approve"}, headers=hdr(r, "support_engineer"))
    assert d.status_code == 200 and d.json()["status"] == "active" and d.json()["approved_by"] == r
    assert pid in [x["pattern_id"] for x in client.get("/api/v1/memory/patterns", headers=hdr(other)).json()["patterns"]]
    envelope_ok(client.post(f"/api/v1/memory/patterns/{pid}/decision", json={"decision": "reject"},
                            headers=hdr(user(), "admin")), 409, "CONFLICT")
    envelope_ok(client.delete(f"/api/v1/memory/patterns/{pid}", headers=hdr(other)), 403, "FORBIDDEN")
    assert client.delete(f"/api/v1/memory/patterns/{pid}", headers=hdr(p)).status_code == 204
    envelope_ok(client.delete(f"/api/v1/memory/patterns/{pid}", headers=hdr(p)), 404, "NOT_FOUND")


def test_admin_may_delete_any_pattern(client, migrated_db_url):
    p = user()
    pid = client.post("/api/v1/memory/patterns", json=proposal(thread(migrated_db_url, p)), headers=hdr(p)).json()["pattern_id"]
    assert client.delete(f"/api/v1/memory/patterns/{pid}", headers=hdr(user(), "admin")).status_code == 204


def test_pattern_for_a_foreign_thread_is_404(client, migrated_db_url):
    tid = thread(migrated_db_url, user())
    envelope_ok(client.post("/api/v1/memory/patterns", json=proposal(tid), headers=hdr(user())), 404, "NOT_FOUND")


@pytest.mark.parametrize("field,value,kind", [
    ("text", "Set client_secret=" + "s3cr3t" * 3 + " first", "secret_field"),
    ("text", "Ask " + "ops" + "@" + "example.com", "email"),
    ("title", "Merchant 0812" + "34567890", "phone"),
    ("text", "Trx " + "9" * 14, "long_digit_run"),
])
def test_unsafe_pattern_text_is_refused_not_redacted(client, migrated_db_url, field, value, kind):
    p = user()
    tid = thread(migrated_db_url, p)
    r = client.post("/api/v1/memory/patterns", json=proposal(tid, **{field: value}), headers=hdr(p))
    envelope_ok(r, 422, "INVALID_ARGUMENT")
    assert {"field": field, "reason": f"refused:{kind}"} in r.json()["field_errors"]
    assert value not in r.text
    with psycopg.connect(migrated_db_url) as c:
        assert c.execute("SELECT count(*) FROM long_term_memory WHERE proposed_by = %s", (p,)).fetchone()[0] == 0


@pytest.mark.parametrize("role", ["developer", "kb_maintainer"])
def test_non_approver_may_not_decide(client, migrated_db_url, role):
    p = user()
    pid = client.post("/api/v1/memory/patterns", json=proposal(thread(migrated_db_url, p)), headers=hdr(p)).json()["pattern_id"]
    envelope_ok(client.post(f"/api/v1/memory/patterns/{pid}/decision", json={"decision": "approve"},
                            headers=hdr(user(), role)), 403, "FORBIDDEN")


def test_proposer_may_not_approve_own_pattern_even_as_admin(client, migrated_db_url):
    p = user()
    pid = client.post("/api/v1/memory/patterns", json=proposal(thread(migrated_db_url, p)),
                      headers=hdr(p, "admin")).json()["pattern_id"]
    envelope_ok(client.post(f"/api/v1/memory/patterns/{pid}/decision", json={"decision": "approve"},
                            headers=hdr(p, "admin")), 403, "FORBIDDEN")


def test_decide_unknown_pattern_is_404(client):
    envelope_ok(client.post(f"/api/v1/memory/patterns/{uuid.uuid4()}/decision", json={"decision": "approve"},
                            headers=hdr(user(), "admin")), 404, "NOT_FOUND")


# --- feedback ------------------------------------------------------------------------------------

def test_feedback_owner_only_records_user_feedback(client, migrated_db_url):
    o = user()
    tid = thread(migrated_db_url, o)
    before = len(client.recorder.scores)
    assert client.post(f"/api/v1/threads/{tid}/feedback", json={"rating": "up"}, headers=hdr(o)).status_code == 204
    run_id, name, value, kw = client.recorder.scores[-1]
    assert (name, value, kw.get("session_id")) == ("user_feedback", "up", tid) and len(client.recorder.scores) == before + 1
    envelope_ok(client.post(f"/api/v1/threads/{tid}/feedback", json={"rating": "up"}, headers=hdr(user())), 404, "NOT_FOUND")
    envelope_ok(client.post(f"/api/v1/threads/{tid}/feedback", json={"rating": "meh"}, headers=hdr(o)), 422, "INVALID_ARGUMENT")
    assert len(client.recorder.scores) == before + 1


def test_feedback_without_a_run_is_409(client, migrated_db_url):
    o = user()
    tid = thread(migrated_db_url, o, run_id=False)
    envelope_ok(client.post(f"/api/v1/threads/{tid}/feedback", json={"rating": "down"}, headers=hdr(o)), 409, "CONFLICT")
