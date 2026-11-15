"""scripts/eval_answers.py at its real entry point, CI mode (p9-final spec AC #8; plan step 3)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "eval_answers.py"
SMALL = ROOT / "tests" / "fixtures" / "evaluation" / "golden_small.yaml"
sys.path.insert(0, str(ROOT / "scripts"))


def run(*args, env=None):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", env={**os.environ, **(env or {})}, timeout=300)


def test_ci_mode_writes_answers_file_with_checks(tmp_path):
    r = run("--dataset", str(SMALL), "--allow-unreviewed", "--report-dir", str(tmp_path))
    (report,) = tmp_path.glob("answers-ci-*.json")
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["mode"] == "ci" and data["dataset_version"] == "0.0.1"
    assert data["items_run"] == 3 and data["unreviewed_skipped"] == 0
    by_id = {rec["id"]: rec for rec in data["records"]}
    assert by_id["ga-002"]["observed_behaviour"] == "refuse"
    assert by_id["ga-003"]["observed_behaviour"] == "not_found"
    for rec in data["records"]:
        assert set(rec["checks"]) == {"citations", "fabricated_crypto", "secret_findings"}
        assert all(len(c["excerpt"]) <= 300 for c in rec["citations"])
    s = data["summary"]
    # the exit code follows the deterministic checks, nothing else
    clean = s["citations_valid"] == s["citations_total"] and s["fabricated_crypto"] == 0 and s["secret_findings"] == 0
    assert r.returncode == (0 if clean else 1), r.stdout + r.stderr


def test_unreviewed_items_are_skipped_without_the_flag(tmp_path):
    run("--dataset", str(SMALL), "--report-dir", str(tmp_path))
    (report,) = tmp_path.glob("answers-ci-*.json")
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["items_run"] == 2 and data["unreviewed_skipped"] == 1
    assert "ga-003" not in {rec["id"] for rec in data["records"]}


def test_live_refuses_allow_unreviewed(tmp_path):
    r = run("--live", "--allow-unreviewed", "--dataset", str(SMALL), "--report-dir", str(tmp_path))
    assert r.returncode == 1 and "CI mode only" in r.stderr and not list(tmp_path.iterdir())


def test_schema_failure_is_one_line_error(tmp_path):
    data = yaml.safe_load(SMALL.read_text(encoding="utf-8"))
    data["items"][0]["expected_behaviour"] = "maybe"
    bad = tmp_path / "golden.yaml"
    bad.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    r = run("--dataset", str(bad), "--report-dir", str(tmp_path))
    assert r.returncode == 1 and r.stderr.startswith("error: dataset fails its schema") and "Traceback" not in r.stderr


def test_live_mode_refuses_when_services_are_down(tmp_path):
    r = run("--live", "--dataset", str(SMALL), "--report-dir", str(tmp_path),
            env={"MCP_AUTH_SECRET": "s" * 48, "VLLM_BASE_URL": "http://127.0.0.1:1/v1", "VLLM_API_KEY": "synthetic"})
    assert r.returncode == 1 and "error: not reachable" in r.stderr and "Traceback" not in r.stderr


def test_exit_code_rule():
    from eval_answers import exit_code
    base = {"items": 3, "citations_total": 2, "citations_valid": 2, "fabricated_crypto": 0, "secret_findings": 0}
    assert exit_code(base) == 0
    assert exit_code({**base, "citations_valid": 1}) == 1
    assert exit_code({**base, "fabricated_crypto": 1}) == 1
    assert exit_code({**base, "secret_findings": 1}) == 1
    assert exit_code({**base, "items": 0}) == 1
