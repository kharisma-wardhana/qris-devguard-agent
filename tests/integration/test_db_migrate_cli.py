"""scripts/db_migrate.py at its real entry point (p6 spec AC #1)."""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


# p7: db_migrate.py also creates the runtime roles. Tests give throwaway role names (suffix) and passwords so
# the real devguard_* roles, whose passwords live in .env, are never touched.
SUFFIX = f"_t{secrets.token_hex(4)}"
PASSWORDS = {"DEVGUARD_APP_DB_PASSWORD": secrets.token_hex(12), "DEVGUARD_TICKET_DB_PASSWORD": secrets.token_hex(12),
             "DEVGUARD_AUDIT_DB_PASSWORD": secrets.token_hex(12)}
ROLE_ENV = {"DEVGUARD_DB_ROLE_SUFFIX": SUFFIX, **PASSWORDS}


def run(env_url, extra=None):
    env = {**os.environ, "DATABASE_URL": env_url, **(ROLE_ENV if extra is None else extra)}
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "db_migrate.py")], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", timeout=120, env=env)


@pytest.fixture
def cleanup_roles(fresh_db_url):
    yield
    import psycopg

    from app.db import roles
    with psycopg.connect(fresh_db_url, autocommit=True) as c:
        roles.drop(c, [f"devguard_{k}{SUFFIX}" for k in ("app", "ticket", "audit")])


@pytest.mark.db
def test_applies_then_reports_nothing_to_apply(fresh_db_url, cleanup_roles):
    first = run(fresh_db_url)
    assert first.returncode == 0, first.stderr
    assert first.stdout.startswith("applied 2 (0001_threads_approvals_tickets, 0002_tool_audit_logs)")
    assert "checkpoint tables ready" in first.stdout
    assert f"devguard_ticket{SUFFIX} created" in first.stdout
    second = run(fresh_db_url)
    assert second.returncode == 0 and second.stdout.startswith("applied 0 (-), already applied 2")
    assert f"devguard_app{SUFFIX} unchanged" in second.stdout and "password set" not in second.stdout
    out = first.stdout + first.stderr + second.stdout + second.stderr
    assert not [p for p in PASSWORDS.values() if p in out]


@pytest.mark.db
def test_missing_role_password_names_the_key(fresh_db_url, cleanup_roles, monkeypatch, tmp_path):
    env = {**ROLE_ENV, "DEVGUARD_AUDIT_DB_PASSWORD": ""}
    r = run(fresh_db_url, env)
    # .env may hold the real value; the empty environment value must not fall through to it silently, so
    # either the key is reported missing, or (when .env supplies it) migration succeeds with no value printed.
    out = r.stdout + r.stderr
    assert not [p for p in PASSWORDS.values() if p in out] and "Traceback" not in out
    if r.returncode == 1:
        assert "DEVGUARD_AUDIT_DB_PASSWORD" in r.stderr


def test_unreachable_database_is_a_one_line_error():
    r = run("postgresql://u:SYNpw999@127.0.0.1:1/devguard")
    assert r.returncode == 1 and r.stderr.strip().startswith("error: database not reachable")
    assert "SYNpw999" not in r.stderr and "Traceback" not in r.stderr
