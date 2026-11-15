"""Release gate: PASS/FAIL on every P0 gate from existing evidence (p9-final spec Behaviour §4; AC #16-#22).

Usage:
  uv run python scripts/release_gate.py                    # evals/reports -> JSON report + docs release report
  uv run python scripts/release_gate.py --no-markdown      # JSON only (and the printed table)

Reads configs/eval_thresholds.yaml and configs/evaluation.yaml and the newest accepted report per gate in
evals/reports/. It runs no eval. A gate without accepted evidence is NOT RUN; evidence older than 14 days or for an
outdated dataset version is STALE; neither is PASS. Writes evals/reports/release-gate-<ts>.json (git-ignored) and
rewrites the generated block of docs/p9-final-evaluation-packaging/release-report.md (committed).

Exit 0 on an overall PASS, 1 on FAIL (any gate FAIL, NOT RUN or STALE), 2 on a configuration or usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MARKDOWN = ROOT / "docs" / "p9-final-evaluation-packaging" / "release-report.md"


def main(argv: list[str] | None = None) -> int:
    from app.evaluation import gate, settings

    parser = argparse.ArgumentParser(description="Grade every release gate from existing evidence.")
    parser.add_argument("--reports-dir", type=Path, default=ROOT / settings()["reports_dir"])
    parser.add_argument("--manifest", type=Path, default=None, help="default: the committed deployment manifest")
    parser.add_argument("--markdown", type=Path, default=MARKDOWN)
    parser.add_argument("--no-markdown", action="store_true")
    parser.add_argument("--now", default=None, help="ISO timestamp to grade freshness against (tests)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    sys.stdout.reconfigure(encoding="utf-8")

    try:
        now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        if not args.reports_dir.is_dir():
            raise gate.GateError(f"reports dir not found: {args.reports_dir}")
        result = gate.run(args.reports_dir, now, args.manifest)
    except (gate.GateError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    for g in result["gates"]:
        print(f"{g['status']:<8} {g['kind']:<8} {g['name']:<28} value {gate._fmt(g['value']):<10} "
              f"threshold {gate._thr(g['threshold'])}" + (f"  ({g['note']})" if g["note"] else ""))
    stamp = now.strftime("%Y-%m-%dT%H%M%S")
    out = args.reports_dir / f"release-gate-{stamp}.json"
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if not args.no_markdown:
        gate.write_markdown(args.markdown, gate.markdown(result))
    print(f"VERDICT: {result['verdict']}" + (f" — not passing: {', '.join(result['not_passing'])}"
                                             if result["not_passing"] else "") + f"  report: {out}")
    return 0 if result["verdict"] == gate.PASS else 1


if __name__ == "__main__":
    sys.exit(main())
