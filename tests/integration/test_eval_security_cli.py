"""scripts/eval_security.py at its real entry point (p7 spec AC #21)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PASSING = "tests/unit/test_security_egress.py::test_custom_allowlist"
MISSING = "tests/unit/test_security_egress.py::test_this_node_does_not_exist"


def run(tmp_path, cases):
    path = tmp_path / "cases.yaml"
    path.write_text(yaml.safe_dump({"version": 9, "cases": cases}), encoding="utf-8")
    report = tmp_path / "report.md"
    report.write_text("# Kept heading\n\nhand-written text\n\n<!-- security-matrix:start -->\nold\n"
                      "<!-- security-matrix:end -->\n\nhand-written tail\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "scripts/eval_security.py"), "--cases", str(path), "--report",
                        str(report), "--report-dir", str(tmp_path / "reports")], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", timeout=300)
    return r, report.read_text(encoding="utf-8"), list((tmp_path / "reports").glob("security-*.json"))


def case(cid, target):
    return {"id": cid, "section": "22.5", "bullet": f"bullet {cid}", "payload": "a | b", "expected": "x",
            "tests": [target]}


def test_all_pass_exits_zero_and_replaces_only_the_generated_block(tmp_path):
    r, report, evidence = run(tmp_path, [case("C1", PASSING)])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1/1 cases PASS" in r.stdout
    assert report.startswith("# Kept heading") and "hand-written tail" in report and "\nold\n" not in report
    assert "| C1 | §22.5 bullet C1 | a \\| b |" in report and "**PASS**" in report
    data = json.loads(evidence[0].read_text(encoding="utf-8"))
    assert data["dataset_version"] == 9 and data["results"][0]["status"] == "PASS"


def test_uncollected_node_is_not_run_and_exits_nonzero(tmp_path):
    r, report, evidence = run(tmp_path, [case("C1", PASSING), case("C2", MISSING)])
    assert r.returncode == 1
    results = {x["id"]: x["status"] for x in json.loads(evidence[0].read_text(encoding="utf-8"))["results"]}
    assert results == {"C1": "PASS", "C2": "NOT RUN"} and "**NOT RUN**" in report
