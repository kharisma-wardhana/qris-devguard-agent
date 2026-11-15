"""scripts/eval_crash_resume.py at its real entry point (p9-final spec AC #23).

Fixture tests stand in for the crash/resume test, so this suite never needs PostgreSQL. The real node is exercised
locally with `uv run python scripts/eval_crash_resume.py` (PASS with Postgres up, NOT RUN without).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "eval_crash_resume.py"
FIX = "tests/fixtures/evaluation/crash"
sys.path.insert(0, str(ROOT / "scripts"))


def run(tmp_path, node):
    return subprocess.run([sys.executable, str(SCRIPT), "--node", f"{FIX}/{node}", "--report-dir", str(tmp_path)],
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=300)


@pytest.mark.parametrize("node,status,code", [("crash_passes.py", "PASS", 0), ("crash_fails.py", "FAIL", 1),
                                              ("crash_skips.py", "NOT RUN", 1)])
def test_status_and_exit_code(tmp_path, node, status, code):
    r = run(tmp_path, node)
    assert r.returncode == code, r.stdout + r.stderr
    assert f"crash/resume: {status}" in r.stdout
    (rep,) = tmp_path.glob("crash-resume-*.json")
    data = json.loads(rep.read_text(encoding="utf-8"))
    assert data["status"] == status and data["nodes"] == [f"{FIX}/{node}"]
    assert "planted failure" not in rep.read_text(encoding="utf-8")       # pytest output is never copied


def test_grade_rule():
    from eval_crash_resume import grade
    assert grade([]) == "NOT RUN"
    assert grade([{"outcome": "skipped", "when": "setup"}]) == "NOT RUN"
    assert grade([{"outcome": "passed", "when": "call"}]) == "PASS"
    assert grade([{"outcome": "passed", "when": "call"}, {"outcome": "failed", "when": "teardown"}]) == "FAIL"
