"""Retention purge (p8 spec Behaviour §6; AC #4, #5, #26, #27; TRD §19.3, the P6 hand-off).

Run as the owner role by `scripts/purge_retention.py`; nothing schedules it. Periods come from
`configs/agent.yaml` `retention`. One transaction, in foreign-key order:

1. LangGraph checkpoint rows (`checkpoint_writes`, `checkpoint_blobs`, `checkpoints`) of every thread whose
   `updated_at` is older than `checkpoint_days` and whose status is not `awaiting_approval`. The checkpoint
   tables carry no timestamp of their own, so the thread row dates them. A checkpoint with no thread row is
   left alone: nothing dates it.
2. Sandbox tickets older than `approval_days`.
3. Approvals older than `approval_days` that are not `pending` and no longer have a ticket.
4. Threads idle for `approval_days`, not `awaiting_approval`, with no approval or ticket left.
5. Memory: rows soft-deleted or past `expires_at`, with their history.

`dry_run` runs exactly the same deletes and rolls the transaction back, so the counts are exact (a later step
depends on an earlier one, which separate SELECT counts would get wrong) and nothing is removed.
Results are counts per table only: never row content.
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg import sql

from app.agent import config

CHECKPOINT_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")
_OLD_THREADS = ("SELECT thread_id FROM agent_threads WHERE updated_at < now() - make_interval(days => %(ck)s) "
                "AND status <> 'awaiting_approval'")
_MEMORY = "deleted_at IS NOT NULL OR expires_at < now()"

STEPS: tuple[tuple[str, str], ...] = (
    *[(t, f"thread_id IN ({_OLD_THREADS})") for t in CHECKPOINT_TABLES],
    ("sandbox_tickets", "created_at < now() - make_interval(days => %(keep)s)"),
    ("approval_requests", "created_at < now() - make_interval(days => %(keep)s) AND status <> 'pending' "
                          "AND NOT EXISTS (SELECT 1 FROM sandbox_tickets t "
                          "WHERE t.approval_id = approval_requests.approval_id)"),
    ("agent_threads", "updated_at < now() - make_interval(days => %(keep)s) AND status <> 'awaiting_approval' "
                      "AND NOT EXISTS (SELECT 1 FROM approval_requests a WHERE a.thread_id = agent_threads.thread_id) "
                      "AND NOT EXISTS (SELECT 1 FROM sandbox_tickets t WHERE t.thread_id = agent_threads.thread_id)"),
    ("long_term_memory_history", f"memory_id IN (SELECT memory_id FROM long_term_memory WHERE {_MEMORY})"),
    ("long_term_memory", _MEMORY),
)


def periods() -> dict[str, int]:
    r = config.agent()["retention"]
    return {"checkpoint_days": int(r["checkpoint_days"]), "approval_days": int(r["approval_days"])}


def _exists(conn: psycopg.Connection, table: str) -> bool:
    return conn.execute("SELECT to_regclass(%s) IS NOT NULL", (table,)).fetchone()[0]


def purge(conn: psycopg.Connection, *, checkpoint_days: int, approval_days: int, dry_run: bool = False) -> dict[str, int]:
    """Rows deleted (or, with `dry_run`, that would be deleted) per table. Raises psycopg errors unchanged;
    a role without DELETE on any step's table fails the whole purge and nothing is deleted."""
    if checkpoint_days < 1 or approval_days < 1:
        raise ValueError("retention periods must be at least one day")
    params: dict[str, Any] = {"ck": checkpoint_days, "keep": approval_days}
    counts: dict[str, int] = {}
    try:
        with conn.transaction():
            for table, where in STEPS:
                if table in CHECKPOINT_TABLES and not _exists(conn, table):
                    continue                              # checkpointer tables not set up in this schema
                cur = conn.execute(sql.SQL("DELETE FROM {} WHERE " + where).format(sql.Identifier(table)), params)
                counts[table] = cur.rowcount
            if dry_run:
                raise psycopg.Rollback()
    except psycopg.Rollback:                              # pragma: no cover - transaction() swallows it
        pass
    return counts
