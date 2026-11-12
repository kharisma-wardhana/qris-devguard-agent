"""Run the p7 security evidence matrix and render it (docs/p7-mcp-hardening/spec.md AC #20, #21, #25).

Usage:
  podman compose up -d postgres                          # database cases need it; a skip is NOT RUN, never PASS
  uv run python scripts/eval_security.py                 # every case in evals/datasets/security_cases.yaml
  uv run python scripts/eval_security.py --live-injection   # the 10 injection cases against the real vLLM (tunnel)

Each case names the pytest nodes that prove it. The script runs them once, with evidence capture on
(DEVGUARD_SECURITY_EVIDENCE, see tests/conftest.py), and grades each case:
  PASS     every node it names passed
  FAIL     any node failed or errored
  NOT RUN  a node was skipped or never collected
Each TRD §13.4 control (the dataset's `controls:`) takes the worst result of its cases, lowered to its `cap` when
the evidence does not cover the whole pass condition.
It writes evals/reports/security-<timestamp>.json and replaces the generated block of
docs/p7-mcp-hardening/security-report.md (between the security-matrix markers). Exit 0 only when every case passed;
exit 2 for a dataset mistake (unknown case ID, invalid cap, a control with neither cases nor a recorded grade).
No secret is printed: case payloads are descriptions, and the evidence holds node IDs and outcomes only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CASES = ROOT / "evals" / "datasets" / "security_cases.yaml"
REPORT = ROOT / "docs" / "p7-mcp-hardening" / "security-report.md"
START, END = "<!-- security-matrix:start -->", "<!-- security-matrix:end -->"
LIVE_NODE = "tests/integration/test_injection.py::test_injection_case"


def run_pytest(targets: list[str], evidence: Path, extra_env: dict[str, str] | None = None) -> int:
    env = {**os.environ, "DEVGUARD_SECURITY_EVIDENCE": str(evidence), **(extra_env or {})}
    cmd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "--no-header", *targets]
    return subprocess.run(cmd, cwd=ROOT, env=env).returncode


def exists(target: str) -> bool:
    """The file exists and, for a ::node, the function is defined in it. A missing node would make pytest abort
    the whole run; it is left out and graded NOT RUN instead."""
    path, _, node = target.partition("::")
    file = ROOT / path
    if not file.is_file():
        return False
    return not node or f"def {node.split('[')[0]}(" in file.read_text(encoding="utf-8")


def load_records(evidence: Path) -> list[dict[str, Any]]:
    if not evidence.is_file():
        return []
    return [json.loads(line) for line in evidence.read_text(encoding="utf-8").splitlines() if line.strip()]


def matches(nodeid: str, target: str) -> bool:
    return nodeid == target or nodeid.startswith(target + "::") or nodeid.startswith(target + "[")


def grade(case: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    tests, failed, skipped, missing, props = 0, [], [], [], []
    for target in case["tests"]:
        mine = [r for r in records if matches(r["nodeid"], target)]
        if not mine:
            missing.append(target)
        for r in mine:
            if r["outcome"] == "failed":
                failed.append(r["nodeid"])
            elif r["outcome"] == "skipped":
                skipped.append(r["nodeid"])
            elif r["when"] == "call":
                tests += 1
                if r["properties"].get("actual"):
                    props.append(r["properties"]["actual"])
    status = "FAIL" if failed else "NOT RUN" if (skipped or missing) else "PASS"
    actual = f"{tests} tests passed" + (f"; {len(failed)} failed" if failed else "") + \
        (f"; {len(skipped)} skipped" if skipped else "") + (f"; {len(missing)} not collected" if missing else "")
    if props:
        actual += ". " + " | ".join(props)
    return {"id": case["id"], "section": case["section"], "bullet": case["bullet"], "payload": case["payload"],
            "expected": case["expected"], "actual": actual, "status": status, "tests": case["tests"],
            "failed": failed, "skipped": skipped, "missing": missing}


RANK = {"FAIL": 0, "NOT RUN": 1, "PARTIAL": 2, "PASS": 3}


class DatasetError(ValueError):
    pass


def grade_controls(controls: list[dict[str, Any]], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """TRD §13.4: a control takes the worst status of its cases, lowered to `cap` when the evidence does not cover
    its whole pass condition. A control with no cases has only its hand-recorded `cap`."""
    by_id = {r["id"]: r for r in results}
    graded = []
    for c in controls:
        unknown = [i for i in c["cases"] if i not in by_id]
        cap = c.get("cap")
        if unknown:
            raise DatasetError(f"control {c['control']!r} names unknown cases: {', '.join(unknown)}")
        if cap is not None and cap not in RANK:
            raise DatasetError(f"control {c['control']!r} has an invalid cap: {cap}")
        if not c["cases"] and cap is None:
            raise DatasetError(f"control {c['control']!r} has no cases and no recorded grade")
        if c["cases"]:
            status = min((by_id[i]["status"] for i in c["cases"]), key=RANK.__getitem__)
            if cap is not None and status == "PASS":
                status = cap
            basis = "measured"
        else:
            status, basis = cap, "recorded by hand"
        notes = [n for n in (c.get("cap_reason") if status == cap else None, c.get("note")) if n]
        graded.append({"control": c["control"], "pass_condition": c["pass_condition"], "cases": c["cases"],
                       "status": status, "basis": basis, "note": " ".join(notes)})
    return graded


def cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render(results: list[dict[str, Any]], meta: dict[str, Any], controls: list[dict[str, Any]] | None = None) -> str:
    lines = [START, "",
             f"*Generated by `scripts/eval_security.py` at {meta['generated_at']} from "
             f"`evals/datasets/security_cases.yaml` v{meta['dataset_version']}: "
             f"{meta['passed']}/{meta['cases']} cases PASS. Do not edit by hand.*", ""]
    if controls:
        counts = {s: sum(c["status"] == s for c in controls) for s in RANK}
        lines += ["### TRD §13.4 go-live checklist", "",
                  f"{len(controls)} controls: " + ", ".join(f"{n} {s}" for s, n in counts.items() if n) + ". "
                  "Each control takes the worst result of its cases (below); PARTIAL means the measured evidence "
                  "does not cover the whole pass condition, and the note says what is missing.", "",
                  "| Control | Pass condition | Cases | Basis | Result | Note |", "|---|---|---|---|---|---|"]
        for c in controls:
            lines.append(f"| {cell(c['control'])} | {cell(c['pass_condition'])} | {', '.join(c['cases']) or '-'} | "
                         f"{c['basis']} | **{c['status']}** | {cell(c['note'])} |")
        lines += ["", "### TRD §22.5 MCP security test set (and §13.4 cases)", ""]
    lines += ["| Case | Requirement | Payload | Expected | Actual | Evidence | Result |",
             "|---|---|---|---|---|---|---|"]
    for r in results:
        evidence = "<br>".join(f"`{t.replace('tests/', '')}`" for t in r["tests"])
        lines.append(f"| {r['id']} | §{r['section']} {cell(r['bullet'])} | {cell(r['payload'])} | "
                     f"{cell(r['expected'])} | {cell(r['actual'])} | {evidence} | **{r['status']}** |")
    lines += ["", END]
    return "\n".join(lines)


def write_report(path: Path, block: str) -> None:
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        if START in text and END in text:
            text = text[:text.index(START)] + block + text[text.index(END) + len(END):]
        else:
            text = text.rstrip("\n") + "\n\n" + block + "\n"
    else:
        text = "# Security evidence (p7-mcp-hardening)\n\n" + block + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def live_injection(report_dir: Path) -> int:
    """The 10 injection cases with DEVGUARD_LIVE_LLM=1: harness tools, real vLLM (spec AC #20)."""
    import asyncio

    sys.path.insert(0, str(ROOT / "scripts"))
    from api_server import _load_env_file  # same .env handling as the API and eval_agent.py --live

    from app.agent.llm import VllmLLM
    _load_env_file()                       # VLLM_* into os.environ; the pytest child inherits them
    if not asyncio.run(VllmLLM().ping()):
        print("error: vLLM is not reachable at VLLM_BASE_URL; start the pod and scripts/tunnel.sh first",
              file=sys.stderr)
        return 1
    stamp = dt.datetime.now().strftime("%Y-%m-%dT%H%M%S")
    with tempfile.TemporaryDirectory() as tmp:
        evidence = Path(tmp) / "evidence.jsonl"
        code = run_pytest([LIVE_NODE], evidence, {"DEVGUARD_LIVE_LLM": "1"})
        records = [r for r in load_records(evidence) if r["when"] == "call"]
    cases = [{"nodeid": r["nodeid"], "outcome": r["outcome"],
              "result": json.loads(r["properties"]["result"]) if r["properties"].get("result") else None}
             for r in records]
    out = report_dir / f"injection-live-{stamp}.json"
    report_dir.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"generated_at": stamp, "llm": "vllm", "pytest_exit": code, "cases": cases},
                              indent=2, ensure_ascii=False), encoding="utf-8")
    passed = sum(c["outcome"] == "passed" for c in cases)
    echoes = sum(bool(c["result"] and c["result"].get("answer_echoes_attack_phrase")) for c in cases)
    before = sum(bool(c["result"] and c["result"].get("answer_echo_before_fence")) for c in cases)   # p8
    print(f"live injection: {passed}/{len(cases)} cases passed the gate; answer echoed the attack phrase in "
          f"{echoes} (after fencing; P7 before fencing existed: 5/10); composed answer had instruction-like text "
          f"before fencing in {before}; evidence {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    return 0 if code == 0 and passed == len(cases) == 10 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run and render the p7 security evidence matrix.")
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--report-dir", type=Path, default=ROOT / "evals" / "reports")
    parser.add_argument("--live-injection", action="store_true", help="the 10 injection cases on the real vLLM")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    if args.live_injection:
        return live_injection(args.report_dir)

    data = yaml.safe_load(args.cases.read_text(encoding="utf-8"))
    cases = data["cases"]
    try:                                    # a dataset mistake fails here, before a long pytest run
        grade_controls(data.get("controls", []), [{"id": c["id"], "status": "PASS"} for c in cases])
    except DatasetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    targets = sorted({t for c in cases for t in c["tests"] if exists(t)})
    with tempfile.TemporaryDirectory() as tmp:
        evidence = Path(tmp) / "evidence.jsonl"
        code = run_pytest(targets, evidence)
        records = load_records(evidence)
    results = [grade(c, records) for c in cases]
    controls = grade_controls(data.get("controls", []), results)
    stamp = dt.datetime.now().strftime("%Y-%m-%dT%H%M%S")
    meta = {"generated_at": stamp, "dataset_version": data.get("version"), "cases": len(results),
            "passed": sum(r["status"] == "PASS" for r in results), "pytest_exit": code,
            "nodes": len(records)}
    args.report_dir.mkdir(parents=True, exist_ok=True)
    out = args.report_dir / f"security-{stamp}.json"
    out.write_text(json.dumps({**meta, "results": results, "controls": controls}, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    write_report(args.report, render(results, meta, controls))
    for r in results:
        print(f"{r['id']:<12} {r['status']:<8} {r['bullet']}")
    for c in controls:
        print(f"§13.4 {c['status']:<8} {c['control']} ({c['basis']})")
    print(f"{meta['passed']}/{meta['cases']} cases PASS; evidence {out.name}; report {args.report.name}")
    return 0 if meta["passed"] == meta["cases"] else 1


if __name__ == "__main__":
    sys.exit(main())
