"""scripts/purge_retention.py at its real entry point (p8 spec AC #26, #27)."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from app.db import store as db_store

ROOT = Path(__file__).resolve().parents[2]
LINE = re.compile(r"^(?:would delete|deleted) \d+ rows (?:from [a-z_]+|in total)$")


def run(env_url, *args):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "purge_retention.py"), *args], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", timeout=120,
                          env={**os.environ, "DATABASE_URL": env_url})


@pytest.fixture
def url(fresh_db_url):
    from app.db import migrate
    with psycopg.connect(fresh_db_url, autocommit=True) as conn:
        migrate.apply(conn)
    asyncio.run(migrate.setup_checkpointer(fresh_db_url))
    return fresh_db_url


def old_thread(url) -> str:
    with db_store.connect(url) as c:
        c.execute("INSERT INTO agent_threads (thread_id, owner_id, status, updated_at) VALUES "
                  "('retentioncli0000000', 'dev-1', 'completed', now() - interval '91 days')")
        c.execute("INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint, metadata) "
                  "VALUES ('retentioncli0000000', '', 'c1', '{}', '{}')")
        c.commit()
    return "retentioncli0000000"


def remaining(url, t):
    with db_store.connect(url) as c:
        return (c.execute("SELECT count(*) AS n FROM agent_threads WHERE thread_id = %s", (t,)).fetchone()["n"],
                c.execute("SELECT count(*) AS n FROM checkpoints WHERE thread_id = %s", (t,)).fetchone()["n"])


def check_output(r):
    lines = r.stdout.strip().splitlines()
    assert lines[0].startswith("retention (") and all(LINE.match(x) for x in lines[1:]), lines
    assert "password" not in (r.stdout + r.stderr).lower() and "search_path" not in r.stdout


@pytest.mark.db
def test_dry_run_counts_and_deletes_nothing_then_purge_deletes(url):
    t = old_thread(url)
    r = run(url, "--dry-run")
    assert r.returncode == 0, r.stderr
    check_output(r)
    assert "would delete 1 rows from checkpoints" in r.stdout and "would delete 1 rows from agent_threads" in r.stdout
    assert remaining(url, t) == (1, 1)
    r = run(url)
    assert r.returncode == 0, r.stderr
    check_output(r)
    assert "deleted 1 rows from agent_threads" in r.stdout
    assert remaining(url, t) == (0, 0)


@pytest.mark.db
def test_as_the_app_role_it_fails_and_deletes_nothing(migrated_db_url, db_roles):
    r = run(db_roles.url("app"))
    assert r.returncode == 1 and "nothing was deleted" in r.stderr and "InsufficientPrivilege" in r.stderr
    assert "Traceback" not in r.stderr


def test_unreachable_database_is_a_one_line_error():
    r = run("postgresql://u:SYNpw998@127.0.0.1:1/devguard")
    assert r.returncode == 1 and r.stderr.strip().startswith("error: database not reachable")
    assert "SYNpw998" not in r.stderr and "Traceback" not in r.stderr
