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
