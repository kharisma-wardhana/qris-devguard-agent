"""Database settings, the db fixture and migrations (p6 spec AC #1, #13)."""

from __future__ import annotations

import asyncio
import sys

import pytest

from app.agent import config
from app.db import connect


def test_database_config():
    db = config.agent()["database"]
    assert db["url_env"] == "DATABASE_URL" and db["connect_timeout_seconds"] <= 5


def test_dsn_normalises_scheme_and_errors_hide_the_value(monkeypatch, tmp_path):
    monkeypatch.setattr(connect, "ROOT", tmp_path)                        # no .env here
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:SYNpw123@127.0.0.1:5432/devguard")
    assert connect.dsn() == "postgresql://u:SYNpw123@127.0.0.1:5432/devguard"
    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.delenv("DEVGUARD_TEST_DATABASE_URL", raising=False)
    with pytest.raises(connect.DatabaseNotConfigured) as exc:
        connect.dsn()
    assert "SYNpw123" not in str(exc.value)


def test_conninfo_adds_timeout_and_schema():
    info = connect.conninfo("postgresql://u:pw@127.0.0.1:5432/devguard", schema="p6test_x")
    assert "connect_timeout=" in info and "search_path=p6test_x" in info


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only event loop policy")
def test_selector_loop_on_windows():
    connect.use_selector_loop()
    assert isinstance(asyncio.get_event_loop_policy(), asyncio.WindowsSelectorEventLoopPolicy)


@pytest.mark.db
def test_db_fixture_gives_an_isolated_schema(db_url):
    import psycopg
    with psycopg.connect(db_url) as c:
        schema = c.execute("select current_schema()").fetchone()[0]
    assert schema.startswith("p6test_")


# --- migrations (step 2) ---------------------------------------------------------------------------

def _tables(url):
    import psycopg
    with psycopg.connect(url) as c:
        return {r[0] for r in c.execute("select table_name from information_schema.tables "
                                        "where table_schema = current_schema()").fetchall()}


@pytest.mark.db
def test_migrations_apply_once_then_nothing(fresh_db_url):
    import psycopg

    from app.db import migrate
    with psycopg.connect(fresh_db_url, autocommit=True) as c:
        applied, skipped = migrate.apply(c)
        # p7 adds 0002 (audit table) by design
        assert applied == ["0001_threads_approvals_tickets", "0002_tool_audit_logs"] and skipped == []
        assert migrate.apply(c) == ([], ["0001_threads_approvals_tickets", "0002_tool_audit_logs"])
        assert c.execute("select count(*) from schema_migrations").fetchone()[0] == 2
    assert {"agent_threads", "approval_requests", "sandbox_tickets", "schema_migrations",
            "tool_audit_logs"} <= _tables(fresh_db_url)


@pytest.mark.db
def test_checkpointer_setup_creates_its_tables(fresh_db_url):
    from app.db import migrate
    asyncio.run(migrate.setup_checkpointer(fresh_db_url))
    asyncio.run(migrate.setup_checkpointer(fresh_db_url))                   # idempotent
    assert {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"} <= _tables(fresh_db_url)


# --- p7: audit table constraints -------------------------------------------------------------------

@pytest.mark.db
@pytest.mark.parametrize("cid,sub,role,tool,phase,outcome", [
    ("x" * 32, "dev-1", "developer", "lookup_response_code", "result", "OK"),          # correlation id not hex
    ("a" * 32, "dev 1; drop", "developer", "lookup_response_code", "result", "OK"),     # sub shape
    ("a" * 32, "dev-1", "root", "lookup_response_code", "result", "OK"),                # unknown role
    ("a" * 32, "dev-1", "developer", "Bad Tool", "result", "OK"),                       # tool shape
    ("a" * 32, "dev-1", "developer", "lookup_response_code", "result", "client_secret=abc"),   # free-text outcome
    ("a" * 32, "-", "-", "lookup_response_code", "refused", "UNAUTHENTICATED"),          # refused names no tool
    ("a" * 32, "dev-1", "developer", None, "result", "OK"),                              # tool phase needs a tool
])
def test_audit_table_rejects_anything_but_codes_and_ids(migrated_db_url, cid, sub, role, tool, phase, outcome):
    import psycopg
    with psycopg.connect(migrated_db_url, autocommit=True) as c, pytest.raises(psycopg.errors.CheckViolation):
        c.execute("INSERT INTO tool_audit_logs (correlation_id, actor_sub, actor_role, tool, phase, outcome) "
                  "VALUES (%s, %s, %s, %s, %s, %s)", (cid, sub, role, tool, phase, outcome))


@pytest.mark.db
def test_audit_table_has_no_free_text_column(migrated_db_url):
    import psycopg
    with psycopg.connect(migrated_db_url) as c:
        cols = {r[0] for r in c.execute(
            "select column_name from information_schema.columns "
            "where table_schema = current_schema() and table_name = 'tool_audit_logs'").fetchall()}
    assert cols == {"audit_id", "occurred_at", "correlation_id", "actor_sub", "actor_role", "tool", "phase",
                    "outcome", "duration_ms", "run_id", "approval_id"}
