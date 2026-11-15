"""scripts/release_gate.py at its real entry point (p9-final spec AC #16, #20)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release_gate.py"
sys.path.insert(0, str(ROOT / "tests" / "fixtures" / "evaluation"))
import gate_reports  # noqa: E402

NOW = "2026-11-20T00:00:00+00:00"


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", timeout=120)


def test_fail_verdict_exits_one_and_writes_json_and_markdown(tmp_path):
    reports = tmp_path / "reports"
    gate_reports.write_all(reports)
    md = tmp_path / "release-report.md"
    r = run("--reports-dir", str(reports), "--manifest", str(tmp_path / "none.yaml"), "--markdown", str(md), "--now", NOW)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "VERDICT: FAIL — not passing: reproducible_deployment" in r.stdout
    assert "NOT RUN  gate     reproducible_deployment" in r.stdout
    (out,) = reports.glob("release-gate-*.json")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["verdict"] == "FAIL" and len(data["gates"]) == 15
    assert "**Verdict: FAIL**" in md.read_text(encoding="utf-8")


def test_empty_reports_dir_reads_not_run_everywhere(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    r = run("--reports-dir", str(reports), "--manifest", str(tmp_path / "none.yaml"), "--no-markdown", "--now", NOW)
    assert r.returncode == 1
    data = json.loads(next(reports.glob("release-gate-*.json")).read_text(encoding="utf-8"))
    assert {g["status"] for g in data["gates"]} == {"NOT RUN"}
    assert "PASS " not in r.stdout


def test_missing_reports_dir_and_bad_flag_exit_two(tmp_path):
    r = run("--reports-dir", str(tmp_path / "absent"), "--no-markdown")
    assert r.returncode == 2 and r.stderr.startswith("error: reports dir not found") and "Traceback" not in r.stderr
    assert run("--no-such-flag").returncode == 2
    assert run("--reports-dir", str(tmp_path), "--now", "not-a-date", "--no-markdown").returncode == 2
