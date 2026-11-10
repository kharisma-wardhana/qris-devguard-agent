"""scripts/eval_agent.py at its real entry point, CI mode (p5 spec AC #15)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "evals" / "datasets" / "agent_flows.yaml"


def run(*args):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "eval_agent.py"), *args], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", env={**os.environ}, timeout=300)


def test_all_flows_pass_in_ci_mode_and_report_is_written(tmp_path):
    r = run("--report-dir", str(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "5/5 flows passed (ci mode)" in r.stdout
    (report,) = tmp_path.glob("agent-flows-ci-*.json")
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["passed"] is True and {f["id"] for f in data["flows"]} == {
        f["id"] for f in yaml.safe_load(DATASET.read_text(encoding="utf-8"))["flows"]}
    text = report.read_text(encoding="utf-8")
    assert "SYN-PLACEHOLDER-TOKEN" not in text and "summary" not in text      # no answer text in the report


def test_wrong_expectation_exits_one(tmp_path):
    data = yaml.safe_load(DATASET.read_text(encoding="utf-8"))
    data["flows"][0]["expect"]["validator"]["result"] = "VALID"
    bad = tmp_path / "flows.yaml"
    bad.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    r = run("--dataset", str(bad), "--report-dir", str(tmp_path))
    assert r.returncode == 1 and "FAIL  flow-1-invalid-rsa-signature" in r.stdout and "failed: validator" in r.stdout


def test_unreadable_dataset_is_one_line_error(tmp_path):
    bad = tmp_path / "flows.yaml"
    bad.write_text("flows: [", encoding="utf-8")
    r = run("--dataset", str(bad), "--report-dir", str(tmp_path))
    assert r.returncode == 1 and r.stderr.strip().startswith("error: dataset unreadable") and "Traceback" not in r.stderr


def test_live_mode_refuses_when_services_are_down(tmp_path):
    env_port = "1"            # nothing listens; the script must say so in one line and exit 1
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "eval_agent.py"), "--live", "--report-dir", str(tmp_path)],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=300,
                       env={**os.environ, "MCP_AUTH_SECRET": "s" * 48,
                            "VLLM_BASE_URL": f"http://127.0.0.1:{env_port}/v1", "VLLM_API_KEY": "synthetic"})
    assert r.returncode == 1 and "error: not reachable" in r.stderr and "Traceback" not in r.stderr
