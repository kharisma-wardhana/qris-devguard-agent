"""Crash/resume evidence for the release gate (p9-final spec Architecture; AC #23).

Usage:
  podman compose up -d postgres && uv run python scripts/eval_crash_resume.py

Runs tests/integration/test_crash_resume.py (a `db` test: crash between the approval and the ticket, resume, exactly
one ticket) in a subprocess and writes evals/reports/crash-resume-<ts>.json with PASS, FAIL or NOT RUN. A skipped
test (no PostgreSQL) is NOT RUN, never PASS, the same rule as scripts/eval_security.py. Outcomes come from the
conftest evidence hook (DEVGUARD_SECURITY_EVIDENCE); pytest's own output is not passed through, because a failing
db test prints its fixture URL, which holds the local Postgres password.

Exit 0 only on PASS.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NODES = ["tests/integration/test_crash_resume.py::test_crash_resume_exactly_one_ticket"]


def grade(outcomes: list[dict]) -> str:
    if any(o["outcome"] == "failed" for o in outcomes):
        return "FAIL"
    if any(o["outcome"] == "passed" and o["when"] == "call" for o in outcomes):
        return "PASS"
    return "NOT RUN"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the crash/resume test and record its outcome.")
    parser.add_argument("--node", action="append", default=None, help="pytest node(s); default: the P6 crash test")
    parser.add_argument("--report-dir", type=Path, default=ROOT / "evals" / "reports")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    nodes = args.node or DEFAULT_NODES

    with tempfile.TemporaryDirectory() as tmp:
        evidence = Path(tmp) / "evidence.jsonl"
        env = {**os.environ, "DEVGUARD_SECURITY_EVIDENCE": str(evidence)}
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *nodes], cwd=ROOT,
                              env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
        outcomes = [json.loads(ln) for ln in evidence.read_text(encoding="utf-8").splitlines()] if evidence.exists() else []
    status = grade(outcomes)
    report = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "status": status,
              "pytest_exit": proc.returncode, "nodes": nodes,
              "outcomes": [{"nodeid": o["nodeid"], "when": o["when"], "outcome": o["outcome"]} for o in outcomes]}
    args.report_dir.mkdir(parents=True, exist_ok=True)
    out = args.report_dir / f"crash-resume-{datetime.now().strftime('%Y-%m-%dT%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    hint = "  (start PostgreSQL: podman compose up -d postgres)" if status == "NOT RUN" else ""
    print(f"crash/resume: {status} ({len(outcomes)} outcome record(s), pytest exit {proc.returncode}){hint}  report: {out}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
