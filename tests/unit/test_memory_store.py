"""Memory storage on PostgreSQL (p8 spec AC #1, #4, #5, #6, #8, #10; DB regression tests).

Each test uses its own random owner IDs on the shared migrated throwaway schema.
"""

from __future__ import annotations

import secrets
import uuid

import psycopg
import pytest

from app.db import store as db_store
from app.memory import policy
from app.memory import store

pytestmark = pytest.mark.db


def sub() -> str:
    return "u-" + secrets.token_hex(4)


@pytest.fixture
def conn(migrated_db_url):
    with db_store.connect(migrated_db_url) as c:
        yield c


def history(conn, memory_id) -> list[dict]:
    return conn.execute("SELECT version, content_sanitized FROM long_term_memory_history WHERE memory_id = %s "
                        "ORDER BY version", (memory_id,)).fetchall()


# --- preferences ---------------------------------------------------------------------------------

def test_save_update_unchanged_with_history(conn):
    owner = sub()
    reason, row = store.save_preference(conn, owner, "language", "id")
    assert reason == policy.SAVED and row["version"] == 1
    reason, row = store.save_preference(conn, owner, "language", "en")
    assert reason == policy.UPDATED and row["version"] == 2
    assert history(conn, row["memory_id"]) == [{"version": 1, "content_sanitized": "id"}]
    reason, row = store.save_preference(conn, owner, "language", "en")
    assert reason == policy.UNCHANGED and row["version"] == 2
    assert len(history(conn, row["memory_id"])) == 1
    assert store.get_preferences(conn, owner) == {"language": "en"}


def test_disallowed_value_writes_nothing(conn):
    owner = sub()
    with pytest.raises(ValueError):
        store.save_preference(conn, owner, "language", "fr")
    assert conn.execute("SELECT count(*) AS n FROM long_term_memory WHERE owner_id = %s", (owner,)).fetchone()["n"] == 0


def test_delete_blanks_row_and_history_and_stops_use(conn):
    owner = sub()
    store.save_preference(conn, owner, "detail", "brief")
    _, row = store.save_preference(conn, owner, "detail", "detailed")
    assert store.delete_preference(conn, owner, "detail") is True
    assert store.get_preferences(conn, owner) == {}
    assert store.list_preferences(conn, owner) == []
    raw = conn.execute("SELECT content_sanitized, deleted_at FROM long_term_memory WHERE memory_id = %s",
                       (row["memory_id"],)).fetchone()
    assert raw["content_sanitized"] == "" and raw["deleted_at"] is not None
    assert [h["content_sanitized"] for h in history(conn, row["memory_id"])] == [""]
    assert store.delete_preference(conn, owner, "detail") is False          # already gone
    reason, again = store.save_preference(conn, owner, "detail", "brief")   # saving again is a new row
    assert reason == policy.SAVED and again["memory_id"] != row["memory_id"]


def test_expired_preference_is_not_applied_or_listed(conn):
    owner = sub()
    _, row = store.save_preference(conn, owner, "language", "id")
    conn.execute("UPDATE long_term_memory SET expires_at = now() - interval '1 second' WHERE memory_id = %s",
                 (row["memory_id"],))
    conn.commit()
    assert store.get_preferences(conn, owner) == {} and store.list_preferences(conn, owner) == []
    reason, row2 = store.save_preference(conn, owner, "language", "id")    # saving refreshes the expired row
    assert reason == policy.UPDATED and row2["memory_id"] == row["memory_id"]
    assert store.get_preferences(conn, owner) == {"language": "id"}


def test_isolation_between_users(conn):
    a, b = sub(), sub()
    store.save_preference(conn, a, "language", "id")
    assert store.get_preferences(conn, b) == {} and store.list_preferences(conn, b) == []
    assert store.delete_preference(conn, b, "language") is False
    assert store.get_preferences(conn, a) == {"language": "id"}


def test_one_live_preference_per_key_is_a_constraint(conn):
    owner = sub()
    store.save_preference(conn, owner, "language", "id")
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("INSERT INTO long_term_memory (owner_id, scope, memory_type, pref_key, content_sanitized, status, "
                     "confidence, proposed_by, expires_at) VALUES (%s, 'user', 'user_preference', 'language', 'en', "
                     "'active', 'explicit', %s, now() + interval '1 day')", (owner, owner))
    conn.rollback()


# --- patterns ------------------------------------------------------------------------------------

def propose(conn, proposer, **kw):
    args = {"thread_id": "t" * 20, "run_id": str(uuid.uuid4()), "title": "Timestamp offset",
            "text": "Use +07:00 in X-TIMESTAMP.", "api_family": "qris_mpm", "intent": "timestamp", **kw}
    return store.propose_pattern(conn, proposer=proposer, **args)


def test_pattern_lifecycle_and_four_eyes(conn):
    p, r = sub(), sub()
    row = propose(conn, p)
    assert row["status"] == "pending" and row["scope"] == "team"
    assert row["memory_id"] not in {x["memory_id"] for x in store.patterns_for_run(conn, api_family="qris_mpm", intent=None)}
    assert store.decide_pattern(conn, str(row["memory_id"]), approver=p, approve=True, reason=None)[0] == store.SELF
    outcome, decided = store.decide_pattern(conn, str(row["memory_id"]), approver=r, approve=True, reason="ok")
    assert outcome == store.DECIDED and decided["status"] == "active" and decided["approved_by"] == r
    assert store.decide_pattern(conn, str(row["memory_id"]), approver=sub(), approve=False, reason=None)[0] == store.NOT_PENDING
    shown = store.patterns_for_run(conn, api_family=None, intent="timestamp")
    assert row["memory_id"] in {x["memory_id"] for x in shown}


def test_self_approval_is_rejected_by_the_table(conn):
    p = sub()
    row = propose(conn, p)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE long_term_memory SET status = 'active', approved_by = %s WHERE memory_id = %s",
                     (p, row["memory_id"]))
    conn.rollback()


def test_active_pattern_needs_a_reviewer_in_the_table(conn):
    row = propose(conn, sub())
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE long_term_memory SET status = 'active' WHERE memory_id = %s", (row["memory_id"],))
    conn.rollback()


def test_rejected_pending_deleted_expired_never_shown(conn):
    fam = "fam_" + secrets.token_hex(3)
    pending = propose(conn, sub(), api_family=fam)
    rejected = propose(conn, sub(), api_family=fam)
    store.decide_pattern(conn, str(rejected["memory_id"]), approver=sub(), approve=False, reason=None)
    deleted = propose(conn, sub(), api_family=fam)
    store.decide_pattern(conn, str(deleted["memory_id"]), approver=sub(), approve=True, reason=None)
    store.delete_pattern(conn, str(deleted["memory_id"]), sub=deleted["proposed_by"], admin=False)
    expired = propose(conn, sub(), api_family=fam)
    store.decide_pattern(conn, str(expired["memory_id"]), approver=sub(), approve=True, reason=None)
    conn.execute("UPDATE long_term_memory SET expires_at = now() - interval '1 second' WHERE memory_id = %s",
                 (expired["memory_id"],))
    conn.commit()
    assert store.patterns_for_run(conn, api_family=fam, intent=None) == []
    assert pending["memory_id"] not in {x["memory_id"] for x in store.list_patterns(conn, sub(), approver=False)}


def test_listing_visibility(conn):
    p = sub()
    row = propose(conn, p)
    assert row["memory_id"] in {x["memory_id"] for x in store.list_patterns(conn, p, approver=False)}
    assert row["memory_id"] in {x["memory_id"] for x in store.list_patterns(conn, sub(), approver=True)}
    assert row["memory_id"] not in {x["memory_id"] for x in store.list_patterns(conn, sub(), approver=False)}


def test_pattern_delete_rules_and_blanking(conn):
    p = sub()
    row = propose(conn, p, text="Trailing newline in minified body breaks the signature.")
    mid = str(row["memory_id"])
    assert store.delete_pattern(conn, mid, sub=sub(), admin=False) == store.FORBIDDEN
    assert store.delete_pattern(conn, mid, sub=p, admin=False) == store.DELETED
    raw = conn.execute("SELECT title, content_sanitized FROM long_term_memory WHERE memory_id = %s", (mid,)).fetchone()
    assert raw == {"title": "", "content_sanitized": ""}
    assert store.delete_pattern(conn, mid, sub=p, admin=False) == store.NOT_FOUND
    other = propose(conn, sub())
    assert store.delete_pattern(conn, str(other["memory_id"]), sub=sub(), admin=True) == store.DELETED


def test_no_tags_no_patterns(conn):
    assert store.patterns_for_run(conn, api_family=None, intent=None) == []
