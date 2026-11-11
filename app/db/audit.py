"""MCP tool-call audit trail (p7 spec Behaviour §1, AC #1-#6; TRD §13.4 Auditability).

- `AuditWriter.write(...)` inserts one row through the insert-only `devguard_audit` role. It returns False
  (and logs the exception type only) when the insert fails, so the caller decides: the write tool fails closed,
  read tools carry on. One reused autocommit connection under a lock; reconnects once on failure.
- `purge(conn, days)` runs as the owner (`scripts/purge_audit.py`) and deletes rows older than `days`.

A row holds identifiers and codes only (`0002_tool_audit_logs.sql` CHECKs them); there is no column for an
argument, message, payload or token.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from typing import Any

import psycopg

log = logging.getLogger("devguard.audit")

OUTCOMES = frozenset({
    "ATTEMPT", "OK", "UNAUTHENTICATED", "FORBIDDEN", "INVALID_ARGUMENT", "RATE_LIMITED", "PAYLOAD_TOO_LARGE",
    "SEARCH_UNAVAILABLE", "INTERNAL_ERROR", "TICKET_STORE_UNAVAILABLE", "APPROVAL_INVALID", "APPROVAL_MISMATCH",
    "APPROVAL_EXPIRED", "APPROVAL_NOT_USABLE", "AUDIT_UNAVAILABLE",
})
PHASES = frozenset({"attempt", "result", "refused"})
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

INSERT = ("INSERT INTO tool_audit_logs (correlation_id, actor_sub, actor_role, tool, phase, outcome, duration_ms, "
          "run_id, approval_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)")


def valid_uuid(value: str | None) -> str | None:
    """`value` if it is a lowercase UUID, else None (a bad header is dropped, never stored)."""
    return value if isinstance(value, str) and UUID.match(value) else None


class AuditWriter:
    def __init__(self, connect: Callable[[], psycopg.Connection]) -> None:
        self._connect = connect
        self._conn: psycopg.Connection | None = None
        self._lock = threading.Lock()

    def _close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except psycopg.Error:
                pass
        self._conn = None

    def write(self, *, correlation_id: str, sub: str, role: str, tool: str | None, phase: str, outcome: str,
              duration_ms: int | None = None, run_id: str | None = None, approval_id: str | None = None) -> bool:
        if phase not in PHASES or outcome not in OUTCOMES:
            log.error("audit row refused locally phase=%s outcome=%s", phase, outcome if outcome in OUTCOMES else "?")
            return False
        row = (correlation_id, sub, role, tool, phase, outcome, duration_ms, valid_uuid(run_id),
               valid_uuid(approval_id))
        with self._lock:
            for _ in range(2):
                try:
                    if self._conn is None or self._conn.closed:
                        self._conn = self._connect()
                    self._conn.execute(INSERT, row)
                    return True
                except psycopg.Error as exc:
                    log.error("audit write failed type=%s", type(exc).__name__)
                    self._close()
        return False

    def close(self) -> None:
        with self._lock:
            self._close()


def connector(conninfo: str) -> Callable[[], psycopg.Connection]:
    return lambda: psycopg.connect(conninfo, autocommit=True)


def purge(conn: psycopg.Connection, days: int) -> int:
    if days < 1:
        raise ValueError("retention must be at least one day")
    cur = conn.execute("DELETE FROM tool_audit_logs WHERE occurred_at < now() - make_interval(days => %s)", (days,))
    return cur.rowcount


def rows(conn: psycopg.Connection, **where: Any) -> list[dict[str, Any]]:
    """Tests and the report: audit rows matching equality filters (owner connection)."""
    from psycopg.rows import dict_row
    allowed = {"correlation_id", "actor_sub", "tool", "phase", "outcome", "run_id", "approval_id"}
    if not set(where) <= allowed:
        raise ValueError("unknown filter")
    clause = " AND ".join(f"{k} = %s" for k in where) or "true"
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT * FROM tool_audit_logs WHERE {clause} ORDER BY occurred_at, phase", tuple(where.values()))
        return cur.fetchall()
