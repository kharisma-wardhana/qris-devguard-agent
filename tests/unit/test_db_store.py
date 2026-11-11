"""Actions (hashing) and the owner-scoped store (p6 spec AC #8, #11; DB regression tests)."""

from __future__ import annotations

import secrets
import uuid
from datetime import timedelta

import psycopg
import pytest

from app.db import actions, store

PAYLOAD = {"title": "Signature rejected on access token", "body": "SYN body", "evidence_ids": ["CIT-000002", "CIT-000001"],
           "priority": "medium"}


# --- actions (no database) -----------------------------------------------------------------------

def test_normalize_sorts_dedupes_strips_and_nfc():
    n = actions.normalize({"title": "  Café ", "body": "b ", "evidence_ids": ["b", "a", "a", " "], "priority": "low"})
    assert n == {"title": "Café", "body": "b", "evidence_ids": ["a", "b"], "priority": "low"}


def test_same_payload_same_hash_and_key():
    shuffled = {**PAYLOAD, "evidence_ids": list(reversed(PAYLOAD["evidence_ids"])), "title": PAYLOAD["title"] + "  "}
    assert actions.action_hash(PAYLOAD) == actions.action_hash(shuffled)
    assert actions.idempotency_key("t" * 32, PAYLOAD) == actions.idempotency_key("t" * 32, shuffled)


@pytest.mark.parametrize("field,value", [("title", "other"), ("body", "other"), ("evidence_ids", ["CIT-000009"]),
                                         ("priority", "high")])
def test_any_field_change_changes_hash_and_key(field, value):
    changed = {**PAYLOAD, field: value}
    assert actions.action_hash(changed) != actions.action_hash(PAYLOAD)
    assert actions.idempotency_key("t" * 32, changed) != actions.idempotency_key("t" * 32, PAYLOAD)


def test_key_includes_thread_id():
    assert actions.idempotency_key("a" * 32, PAYLOAD) != actions.idempotency_key("b" * 32, PAYLOAD)


# --- store (database) ----------------------------------------------------------------------------

def tid():
    return secrets.token_urlsafe(24)


@pytest.fixture
def conn(migrated_db_url):
    with store.connect(migrated_db_url) as c:
        yield c


def pending(conn, owner="dev-1", payload=PAYLOAD, ttl=30):
    t = tid()
    store.create_thread(conn, t, owner)
    a = store.get_or_create_pending(conn, thread_id=t, run_id=str(uuid.uuid4()), requester_id=owner,
                                    requester_role="developer", payload=payload, ttl_minutes=ttl)
    return t, a


@pytest.mark.db
def test_threads_are_owner_scoped(conn):
    t = tid()
    store.create_thread(conn, t, "dev-1")
    assert store.get_thread(conn, t, "dev-1")["status"] == "idle"
    assert store.get_thread(conn, t, "dev-2") is None and store.get_thread(conn, tid(), "dev-1") is None


@pytest.mark.db
def test_get_or_create_pending_is_idempotent(conn):
    t, a = pending(conn)
    again = store.get_or_create_pending(conn, thread_id=t, run_id=str(uuid.uuid4()), requester_id="dev-1",
                                        requester_role="developer", payload=PAYLOAD, ttl_minutes=30)
    assert again["approval_id"] == a["approval_id"]
    n = conn.execute("select count(*) as n from approval_requests where thread_id = %s", (t,)).fetchone()["n"]
    assert n == 1


@pytest.mark.db
def test_second_pending_for_same_action_refused_by_index(conn):
    t, a = pending(conn)
    sql = ("insert into approval_requests (approval_id, thread_id, run_id, requester_id, requester_role, action_type, "
           "action_payload, action_hash, status, expires_at) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())")
    with pytest.raises(psycopg.errors.UniqueViolation):
        with conn.transaction():
            conn.execute(sql, (uuid.uuid4(), t, uuid.uuid4(), "dev-1", "developer", "create_support_ticket",
                               psycopg.types.json.Jsonb({}), a["action_hash"], "pending"))


@pytest.mark.db
def test_decide_rules(conn):
    _, a = pending(conn)
    assert store.decide(conn, str(a["approval_id"]), "dev-1", "approve")[0] == store.SELF
    assert store.decide(conn, str(uuid.uuid4()), "sup-1", "approve")[0] == store.NOT_FOUND
    outcome, row = store.decide(conn, str(a["approval_id"]), "sup-1", "approve", "looks right")
    assert outcome == store.DECIDED and row["status"] == "approved" and row["approver_id"] == "sup-1"
    assert store.decide(conn, str(a["approval_id"]), "sup-2", "reject")[0] == store.NOT_PENDING


@pytest.mark.db
def test_decide_after_expiry_marks_expired(conn):
    _, a = pending(conn)
    with conn.transaction():
        conn.execute("update approval_requests set expires_at = now() - interval '1 minute' where approval_id = %s",
                     (a["approval_id"],))
    outcome, row = store.decide(conn, str(a["approval_id"]), "sup-1", "approve")
    assert outcome == store.EXPIRED and row["status"] == "expired"


@pytest.mark.db
def test_four_eyes_is_a_table_constraint(conn):
    _, a = pending(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            conn.execute("update approval_requests set approver_id = requester_id where approval_id = %s",
                         (a["approval_id"],))


@pytest.mark.db
def test_duplicate_idempotency_key_refused_by_constraint(conn):
    t, a = pending(conn)
    store.decide(conn, str(a["approval_id"]), "sup-1", "approve")
    key = actions.idempotency_key(t, PAYLOAD)
    outcome, _ = store.create_ticket(conn, approval_id=str(a["approval_id"]), thread_id=t, caller_id="dev-1",
                                     payload=PAYLOAD, idempotency_key=key)
    assert outcome == store.CREATED
    sql = ("insert into sandbox_tickets (ticket_id, idempotency_key, approval_id, thread_id, requester_id, title, "
           "body_sanitized, evidence_ids, priority) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)")
    with pytest.raises(psycopg.errors.UniqueViolation):
        with conn.transaction():
            conn.execute(sql, (uuid.uuid4(), key, a["approval_id"], t, "dev-1", "t", "b",
                               psycopg.types.json.Jsonb([]), "low"))
    assert store.count_tickets(conn, key) == 1


@pytest.mark.db
def test_stored_payload_is_normalized(conn):
    _, a = pending(conn, payload={**PAYLOAD, "title": "  spaced  "})
    assert a["action_payload"]["title"] == "spaced"
    assert a["expires_at"] - a["created_at"] <= timedelta(minutes=31)
