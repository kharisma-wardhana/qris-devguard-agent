"""scripts/db_migrate.py at its real entry point (p6 spec AC #1)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def run(env_url):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "db_migrate.py")], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", timeout=120, env={**os.environ, "DATABASE_URL": env_url})


@pytest.mark.db
def test_applies_then_reports_nothing_to_apply(fresh_db_url):
    first = run(fresh_db_url)
    assert first.returncode == 0, first.stderr
    assert first.stdout.startswith("applied 1 (0001_threads_approvals_tickets)")
    assert "checkpoint tables ready" in first.stdout
    second = run(fresh_db_url)
    assert second.returncode == 0 and second.stdout.startswith("applied 0 (-), already applied 1")
    assert "password" not in (first.stdout + first.stderr).lower()


def test_unreachable_database_is_a_one_line_error():
    r = run("postgresql://u:SYNpw999@127.0.0.1:1/devguard")
    assert r.returncode == 1 and r.stderr.strip().startswith("error: database not reachable")
    assert "SYNpw999" not in r.stderr and "Traceback" not in r.stderr
