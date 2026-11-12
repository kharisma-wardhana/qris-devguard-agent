"""Owner-scoped queries for threads, approvals and sandbox tickets (p6 spec).

Sync psycopg: the API runs these in a worker thread and the MCP tool runs them in its threaded handler.
Role checks (who may decide) live in the API; the four-eyes rule is also a table constraint.
`create_ticket` is the single place a side effect happens, and it re-checks the approval under a row lock.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.db import actions

# create_ticket outcomes
CREATED, ALREADY = "created", "already_created"
INVALID, MISMATCH, EXPIRED, NOT_USABLE = "APPROVAL_INVALID", "APPROVAL_MISMATCH", "APPROVAL_EXPIRED", "APPROVAL_NOT_USABLE"
# decide outcomes
DECIDED, NOT_FOUND, NOT_PENDING, SELF = "decided", "not_found", "not_pending", "self_approval"


def now() -> datetime:
    return datetime.now(timezone.utc)


def connect(conninfo: str) -> psycopg.Connection:
    return psycopg.connect(conninfo, row_factory=dict_row)


# --- threads -------------------------------------------------------------------------------------

def create_thread(conn: psycopg.Connection, thread_id: str, owner_id: str) -> dict[str, Any]:
    with conn.transaction():
        return conn.execute("INSERT INTO agent_threads (thread_id, owner_id) VALUES (%s, %s) RETURNING *",
                            (thread_id, owner_id)).fetchone()


def get_thread(conn: psycopg.Connection, thread_id: str, owner_id: str) -> dict[str, Any] | None:
    """None for an unknown thread *and* for someone else's thread (no existence leak, T11)."""
    return conn.execute("SELECT * FROM agent_threads WHERE thread_id = %s AND owner_id = %s",
                        (thread_id, owner_id)).fetchone()


def thread_owner(conn: psycopg.Connection, thread_id: str) -> str | None:
    row = conn.execute("SELECT owner_id FROM agent_threads WHERE thread_id = %s", (thread_id,)).fetchone()
    return row["owner_id"] if row else None


def list_threads(conn: psycopg.Connection, owner_id: str, limit: int) -> list[dict[str, Any]]:
    """p9: the owner's threads only, most recently updated first (agent_threads_owner index)."""
    return conn.execute("SELECT thread_id, status, created_at, updated_at FROM agent_threads WHERE owner_id = %s "
                        "ORDER BY updated_at DESC, thread_id LIMIT %s", (owner_id, limit)).fetchall()


def pending_approvals(conn: psycopg.Connection, limit: int) -> list[dict[str, Any]]:
    """p9: pending approvals, newest first, for an approver's queue. Expiry is still enforced by decide()."""
    return conn.execute("SELECT * FROM approval_requests WHERE status = 'pending' ORDER BY created_at DESC, "
                        "approval_id LIMIT %s", (limit,)).fetchall()


def set_thread(conn: psycopg.Connection, thread_id: str, status: str, run_id: str | None = None) -> None:
    with conn.transaction():
        conn.execute("UPDATE agent_threads SET status = %s, last_run_id = COALESCE(%s::uuid, last_run_id), "
                     "updated_at = now() WHERE thread_id = %s", (status, run_id, thread_id))


# --- approvals -----------------------------------------------------------------------------------

def get_or_create_pending(conn: psycopg.Connection, *, thread_id: str, run_id: str, requester_id: str,
                          requester_role: str, payload: dict[str, Any], ttl_minutes: int) -> dict[str, Any]:
    """The pending approval for this exact action on this thread; created once (re-run on resume is a no-op)."""
    norm, h = actions.normalize(payload), actions.action_hash(payload)
    with conn.transaction():
        row = conn.execute(
            "INSERT INTO approval_requests (approval_id, thread_id, run_id, requester_id, requester_role, action_type, "
            "action_payload, action_hash, status, expires_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s) "
            "ON CONFLICT (thread_id, action_hash) WHERE status = 'pending' DO NOTHING RETURNING *",
            (uuid.uuid4(), thread_id, run_id, requester_id, requester_role, actions.ACTION_TYPE, Jsonb(norm), h,
             now() + timedelta(minutes=ttl_minutes))).fetchone()
        if row is None:
            row = conn.execute("SELECT * FROM approval_requests WHERE thread_id = %s AND action_hash = %s "
                               "AND status = 'pending'", (thread_id, h)).fetchone()
    return row


def get_approval(conn: psycopg.Connection, approval_id: str) -> dict[str, Any] | None:
    return conn.execute("SELECT * FROM approval_requests WHERE approval_id = %s", (approval_id,)).fetchone()


def latest_approval(conn: psycopg.Connection, thread_id: str) -> dict[str, Any] | None:
    return conn.execute("SELECT * FROM approval_requests WHERE thread_id = %s ORDER BY created_at DESC LIMIT 1",
                        (thread_id,)).fetchone()


def decide(conn: psycopg.Connection, approval_id: str, approver_id: str, decision: str,
           reason: str | None = None) -> tuple[str, dict[str, Any] | None]:
    """Record approve/reject on a pending, unexpired approval. Expired -> status expired, outcome EXPIRED."""
    if decision not in ("approve", "reject"):
        raise ValueError("decision must be approve or reject")
    with conn.transaction():
        row = conn.execute("SELECT * FROM approval_requests WHERE approval_id = %s FOR UPDATE", (approval_id,)).fetchone()
        if row is None:
            return NOT_FOUND, None
        if row["requester_id"] == approver_id:
            return SELF, row
        if row["status"] != "pending":
            return NOT_PENDING, row
        if row["expires_at"] <= now():
            row = conn.execute("UPDATE approval_requests SET status = 'expired' WHERE approval_id = %s RETURNING *",
                               (approval_id,)).fetchone()
            return EXPIRED, row
        row = conn.execute(
            "UPDATE approval_requests SET status = %s, approver_id = %s, decision_reason = %s, decided_at = now() "
            "WHERE approval_id = %s RETURNING *",
            ("approved" if decision == "approve" else "rejected", approver_id, (reason or None), approval_id)).fetchone()
    return DECIDED, row


# --- the side effect -----------------------------------------------------------------------------

def create_ticket(conn: psycopg.Connection, *, approval_id: str, thread_id: str, caller_id: str,
                  payload: dict[str, Any], idempotency_key: str) -> tuple[str, dict[str, Any] | None]:
    """Create the sandbox ticket iff the approval is valid for exactly this action, once.

    Check order (p6 spec): existing ticket for this key+approval -> ALREADY; approval missing or another
    thread/user -> INVALID; hash differs -> MISMATCH; expired -> EXPIRED; not `approved` -> NOT_USABLE;
    else insert, mark the approval executed. The caller verifies idempotency_key == recomputed beforehand.
    """
    norm = actions.normalize(payload)
    with conn.transaction():
        appr = conn.execute("SELECT * FROM approval_requests WHERE approval_id = %s FOR UPDATE", (approval_id,)).fetchone()
        existing = conn.execute("SELECT * FROM sandbox_tickets WHERE idempotency_key = %s", (idempotency_key,)).fetchone()
        if existing is not None and appr is not None and str(existing["approval_id"]) == str(appr["approval_id"]) \
                and appr["thread_id"] == thread_id and appr["requester_id"] == caller_id:
            return ALREADY, existing
        if appr is None or appr["thread_id"] != thread_id or appr["requester_id"] != caller_id:
            return INVALID, None
        if appr["action_hash"] != actions.action_hash(payload):
            return MISMATCH, None
        if appr["status"] == "approved" and appr["expires_at"] <= now():
            conn.execute("UPDATE approval_requests SET status = 'expired' WHERE approval_id = %s", (approval_id,))
            return EXPIRED, None
        if appr["status"] != "approved" or existing is not None:
            return NOT_USABLE, None
        ticket = conn.execute(
            "INSERT INTO sandbox_tickets (ticket_id, idempotency_key, approval_id, thread_id, requester_id, title, "
            "body_sanitized, evidence_ids, priority) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (uuid.uuid4(), idempotency_key, approval_id, thread_id, caller_id, norm["title"], norm["body"],
             Jsonb(norm["evidence_ids"]), norm["priority"])).fetchone()
        conn.execute("UPDATE approval_requests SET status = 'executed', executed_at = now(), ticket_id = %s "
                     "WHERE approval_id = %s", (ticket["ticket_id"], approval_id))
    return CREATED, ticket


def count_tickets(conn: psycopg.Connection, idempotency_key: str) -> int:
    return conn.execute("SELECT count(*) AS n FROM sandbox_tickets WHERE idempotency_key = %s",
                        (idempotency_key,)).fetchone()["n"]
