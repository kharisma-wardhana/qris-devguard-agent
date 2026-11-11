"""Apply app/db/migrations/NNNN_*.sql in order, once each, recorded in schema_migrations (p6 spec AC #1)."""

from __future__ import annotations

from pathlib import Path

import psycopg

MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def pending_files() -> list[Path]:
    return sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql"))


def apply(conn: psycopg.Connection) -> tuple[list[str], list[str]]:
    """(applied, skipped) version names. Each migration runs in its own transaction with its record row."""
    with conn.transaction():
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                     "(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
    applied, skipped = [], []
    for path in pending_files():
        version = path.stem
        if version in done:
            skipped.append(version)
            continue
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
        applied.append(version)
    return applied, skipped


async def setup_checkpointer(conninfo: str) -> None:
    """LangGraph's own versioned checkpoint tables (checkpoint_migrations)."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(conninfo) as saver:
        await saver.setup()
