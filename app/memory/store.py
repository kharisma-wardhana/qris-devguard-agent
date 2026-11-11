"""Long-term memory storage (spec Behaviour §1, Data). Sync psycopg with dict rows, parameterised SQL only.

Runs as devguard_app, which has no DELETE on these tables: a user delete is a soft delete that blanks the text
in the row and in its history; scripts/purge_retention.py hard-deletes later as the owner.
Every preference query is scoped by owner_id (T11). Role checks live in the API; four eyes is also a CHECK.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import psycopg

from app.db.store import now
from app.memory import policy

PREF, PATTERN = "user_preference", "verified_pattern"
# decide_pattern / delete_pattern outcomes
DECIDED, NOT_FOUND, NOT_PENDING, SELF, FORBIDDEN, DELETED = (
    "decided", "not_found", "not_pending", "self_approval", "forbidden", "deleted")

LIVE = "deleted_at IS NULL AND expires_at > now()"


def _ttl(key: str) -> timedelta:
    return timedelta(days=int(policy.cfg()[key]))


# --- preferences ---------------------------------------------------------------------------------

def save_preference(conn: psycopg.Connection, owner: str, key: str, value: str, *,
                    run_id: str | None = None, thread_id: str | None = None) -> tuple[str, dict[str, Any]]:
    """Save or update one preference. Returns (saved|updated|unchanged reason code, row).

    Same value again changes nothing. A new value bumps the version and copies the old value into history.
    An expired but undeleted row is refreshed as a new save of that row (version bump, history kept)."""
    if not policy.is_allowed(key, value):
        raise ValueError("preference key or value is not allowed")
    expires = now() + _ttl("preference_ttl_days")
    with conn.transaction():
        row = conn.execute("SELECT * FROM long_term_memory WHERE owner_id = %s AND memory_type = %s "
                           "AND pref_key = %s AND deleted_at IS NULL FOR UPDATE", (owner, PREF, key)).fetchone()
        if row is None:
            row = conn.execute(
                "INSERT INTO long_term_memory (owner_id, scope, memory_type, pref_key, content_sanitized, status, "
                "confidence, proposed_by, source_thread_id, source_run_id, expires_at) "
                "VALUES (%s, 'user', %s, %s, %s, 'active', 'explicit', %s, %s, %s, %s) RETURNING *",
                (owner, PREF, key, value, owner, thread_id, run_id, expires)).fetchone()
            return policy.SAVED, row
        if row["content_sanitized"] == value and row["expires_at"] > now():
            return policy.UNCHANGED, row
        conn.execute("INSERT INTO long_term_memory_history (memory_id, version, content_sanitized, changed_by) "
                     "VALUES (%s, %s, %s, %s)", (row["memory_id"], row["version"], row["content_sanitized"], owner))
        row = conn.execute(
            "UPDATE long_term_memory SET content_sanitized = %s, version = version + 1, updated_at = now(), "
            "expires_at = %s, source_thread_id = coalesce(%s, source_thread_id), "
            "source_run_id = coalesce(%s, source_run_id) WHERE memory_id = %s RETURNING *",
            (value, expires, thread_id, run_id, row["memory_id"])).fetchone()
        return policy.UPDATED, row


def get_preferences(conn: psycopg.Connection, owner: str) -> dict[str, str]:
    """The owner's active, unexpired preferences as {key: value}. Never another user's."""
    rows = conn.execute(f"SELECT pref_key, content_sanitized FROM long_term_memory WHERE owner_id = %s "
                        f"AND memory_type = %s AND {LIVE}", (owner, PREF)).fetchall()
    return {r["pref_key"]: r["content_sanitized"] for r in rows if policy.is_allowed(r["pref_key"], r["content_sanitized"])}


def list_preferences(conn: psycopg.Connection, owner: str) -> list[dict[str, Any]]:
    return conn.execute(f"SELECT memory_id, pref_key, content_sanitized, version, created_at, updated_at, expires_at "
                        f"FROM long_term_memory WHERE owner_id = %s AND memory_type = %s AND {LIVE} "
                        f"ORDER BY pref_key", (owner, PREF)).fetchall()


def _blank(conn: psycopg.Connection, memory_id: Any) -> None:
    conn.execute("UPDATE long_term_memory SET content_sanitized = '', title = '', deleted_at = now(), "
                 "updated_at = now() WHERE memory_id = %s", (memory_id,))
    conn.execute("UPDATE long_term_memory_history SET content_sanitized = '' WHERE memory_id = %s", (memory_id,))


def delete_preference(conn: psycopg.Connection, owner: str, key: str) -> bool:
    """Soft delete the owner's preference (text blanked, history blanked). False if the owner has none."""
    with conn.transaction():
        row = conn.execute("SELECT memory_id FROM long_term_memory WHERE owner_id = %s AND memory_type = %s "
                           "AND pref_key = %s AND deleted_at IS NULL FOR UPDATE", (owner, PREF, key)).fetchone()
        if row is None:
            return False
        _blank(conn, row["memory_id"])
        return True


# --- verified patterns ---------------------------------------------------------------------------

def propose_pattern(conn: psycopg.Connection, *, proposer: str, thread_id: str, run_id: str | None, title: str,
                    text: str, api_family: str | None, intent: str | None) -> dict[str, Any]:
    """Store a pending pattern. The caller has checked thread ownership and policy.refusal_kinds()."""
    with conn.transaction():
        return conn.execute(
            "INSERT INTO long_term_memory (owner_id, scope, memory_type, title, content_sanitized, api_family, "
            "intent, status, confidence, proposed_by, source_thread_id, source_run_id, expires_at) "
            "VALUES (%s, 'team', %s, %s, %s, %s, %s, 'pending', 'verified', %s, %s, %s, %s) RETURNING *",
            (proposer, PATTERN, title, text, api_family, intent, proposer, thread_id, run_id,
             now() + _ttl("pattern_ttl_days"))).fetchone()


def get_pattern(conn: psycopg.Connection, memory_id: str) -> dict[str, Any] | None:
    return conn.execute("SELECT * FROM long_term_memory WHERE memory_id = %s AND memory_type = %s "
                        "AND deleted_at IS NULL", (memory_id, PATTERN)).fetchone()


def decide_pattern(conn: psycopg.Connection, memory_id: str, *, approver: str, approve: bool,
                   reason: str | None) -> tuple[str, dict[str, Any] | None]:
    """Approve (active, expiry restarts) or reject a pending pattern. The approver may not be the proposer."""
    with conn.transaction():
        row = conn.execute("SELECT * FROM long_term_memory WHERE memory_id = %s AND memory_type = %s "
                           "AND deleted_at IS NULL FOR UPDATE", (memory_id, PATTERN)).fetchone()
        if row is None:
            return NOT_FOUND, None
        if row["proposed_by"] == approver:
            return SELF, row
        if row["status"] != "pending" or row["expires_at"] <= now():
            return NOT_PENDING, row
        if approve:
            row = conn.execute(
                "UPDATE long_term_memory SET status = 'active', approved_by = %s, decision_reason = %s, "
                "updated_at = now(), expires_at = %s WHERE memory_id = %s RETURNING *",
                (approver, reason, now() + _ttl("pattern_ttl_days"), memory_id)).fetchone()
        else:
            row = conn.execute(
                "UPDATE long_term_memory SET status = 'rejected', approved_by = %s, decision_reason = %s, "
                "updated_at = now() WHERE memory_id = %s RETURNING *", (approver, reason, memory_id)).fetchone()
        return DECIDED, row


def list_patterns(conn: psycopg.Connection, viewer: str, *, approver: bool) -> list[dict[str, Any]]:
    """Active patterns for everyone; pending ones for their proposer, and all pending for approvers."""
    return conn.execute(
        f"SELECT memory_id, title, content_sanitized, api_family, intent, status, proposed_by, approved_by, "
        f"created_at, updated_at, expires_at FROM long_term_memory WHERE memory_type = %s AND {LIVE} AND "
        f"(status = 'active' OR (status = 'pending' AND (proposed_by = %s OR %s))) ORDER BY created_at",
        (PATTERN, viewer, approver)).fetchall()


def delete_pattern(conn: psycopg.Connection, memory_id: str, *, sub: str, admin: bool) -> str:
    """Soft delete with blanking. The proposer (any status) or an admin. Others: forbidden; unknown: not_found."""
    with conn.transaction():
        row = conn.execute("SELECT memory_id, proposed_by FROM long_term_memory WHERE memory_id = %s "
                           "AND memory_type = %s AND deleted_at IS NULL FOR UPDATE", (memory_id, PATTERN)).fetchone()
        if row is None:
            return NOT_FOUND
        if row["proposed_by"] != sub and not admin:
            return FORBIDDEN
        _blank(conn, row["memory_id"])
        return DELETED


def patterns_for_run(conn: psycopg.Connection, *, api_family: str | None, intent: str | None) -> list[dict[str, Any]]:
    """Active, unexpired patterns whose api_family or intent tag matches the run (spec AC #9)."""
    if not api_family and not intent:
        return []
    limit = int(policy.cfg()["max_patterns_in_prompt"])
    return conn.execute(
        f"SELECT memory_id, title, content_sanitized FROM long_term_memory WHERE memory_type = %s "
        f"AND status = 'active' AND {LIVE} AND ((api_family IS NOT NULL AND api_family = %s) OR "
        f"(intent IS NOT NULL AND intent = %s)) ORDER BY updated_at DESC LIMIT %s",
        (PATTERN, api_family, intent, limit)).fetchall()
