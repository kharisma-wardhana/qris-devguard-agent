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


def run(tmp_path, cases, controls=None):
    path = tmp_path / "cases.yaml"
    data = {"version": 9, "cases": cases, **({"controls": controls} if controls is not None else {})}
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
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


def control(name, cases, **extra):
    return {"control": name, "pass_condition": f"pc {name}", "cases": cases, **extra}


def test_controls_take_the_worst_case_and_a_cap_never_raises(tmp_path):
    controls = [control("Measured", ["C1"]),
                control("Capped", ["C1"], cap="PARTIAL", cap_reason="half the condition"),
                control("Broken", ["C1", "C2"], cap="PARTIAL", cap_reason="not shown when worse"),
                control("By hand", [], cap="PARTIAL", cap_reason="scan pending")]
    r, report, evidence = run(tmp_path, [case("C1", PASSING), case("C2", MISSING)], controls)
    assert r.returncode == 1
    got = {c["control"]: (c["status"], c["basis"]) for c in json.loads(evidence[0].read_text(encoding="utf-8"))["controls"]}
    assert got == {"Measured": ("PASS", "measured"), "Capped": ("PARTIAL", "measured"),
                   "Broken": ("NOT RUN", "measured"), "By hand": ("PARTIAL", "recorded by hand")}
    assert "### TRD §13.4 go-live checklist" in report and "| Capped | pc Capped | C1 | measured | **PARTIAL** | " \
        "half the condition |" in report
    assert "not shown when worse" not in report


def test_dataset_mistake_exits_2_before_running_pytest(tmp_path):
    for bad in ([control("Typo", ["C9"])], [control("Empty", [])], [control("Cap", ["C1"], cap="MAYBE")]):
        r, _, evidence = run(tmp_path, [case("C1", PASSING)], bad)
        assert r.returncode == 2 and r.stderr.startswith("error: control ") and evidence == [], r.stderr
        assert "passed" not in r.stdout                               # pytest never ran


def test_live_injection_loads_vllm_settings_before_the_ping(tmp_path, monkeypatch):
    """Regression: --live-injection pinged vLLM without reading VLLM_* from .env, so a healthy tunnel was refused."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import api_server
    import eval_security

    from app.agent import llm
    order = []
    monkeypatch.setattr(api_server, "_load_env_file", lambda: order.append("load"))

    async def ping(self):
        order.append("ping")
        return False
    monkeypatch.setattr(llm.VllmLLM, "ping", ping)
    assert eval_security.live_injection(tmp_path) == 1
    assert order == ["load", "ping"]


def test_uncollected_node_is_not_run_and_exits_nonzero(tmp_path):
    r, report, evidence = run(tmp_path, [case("C1", PASSING), case("C2", MISSING)])
    assert r.returncode == 1
    results = {x["id"]: x["status"] for x in json.loads(evidence[0].read_text(encoding="utf-8"))["results"]}
    assert results == {"C1": "PASS", "C2": "NOT RUN"} and "**NOT RUN**" in report
