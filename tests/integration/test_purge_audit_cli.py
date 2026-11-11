"""scripts/purge_audit.py at its real entry point (p7 spec AC #6)."""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

from app.db import audit

ROOT = Path(__file__).resolve().parents[2]


def run(env_url, *args):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "purge_audit.py"), *args], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", timeout=60,
                          env={**os.environ, "DATABASE_URL": env_url})


@pytest.mark.db
def test_purges_only_rows_older_than_retention(migrated_db_url, db_roles):
    import psycopg
    w = audit.AuditWriter(audit.connector(db_roles.url("audit")))
    old, new = secrets.token_hex(16), secrets.token_hex(16)
    for cid in (old, new):
        assert w.write(correlation_id=cid, sub="purge-cli", role="developer", tool="lookup_response_code",
                       phase="result", outcome="OK")
    with psycopg.connect(migrated_db_url, autocommit=True) as c:
        c.execute("update tool_audit_logs set occurred_at = now() - interval '100 days' where correlation_id = %s",
                  (old,))
    r = run(migrated_db_url)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("deleted ") and "older than 90 days" in r.stdout
    with psycopg.connect(migrated_db_url, autocommit=True) as c:
        assert audit.rows(c, correlation_id=old) == [] and len(audit.rows(c, correlation_id=new)) == 1
    assert "password" not in (r.stdout + r.stderr).lower()


def test_days_below_one_refused():
    r = run("postgresql://u:SYNpw1@127.0.0.1:1/x", "--days", "0")
    assert r.returncode == 2 and "at least 1" in r.stderr


def test_unreachable_database_is_a_one_line_error():
    r = run("postgresql://u:SYNpw999@127.0.0.1:1/devguard")
    assert r.returncode == 1 and r.stderr.strip().startswith("error: database not reachable")
    assert "SYNpw999" not in r.stderr and "Traceback" not in r.stderr
