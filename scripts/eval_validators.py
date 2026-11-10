"""Run every deterministic-validator fixture and report against the release gate (p4 spec AC #14).

Usage:
  uv run python scripts/eval_validators.py
  uv run python scripts/eval_validators.py --keys-dir <dir> --report-dir <dir> --index <fixtures.yaml>

- Missing test keys are generated first (git-ignored key dir, or --keys-dir).
- Prints a pass/fail line per validator and writes evals/reports/validators-<timestamp>.json
  (IDs, statuses and codes only).
- Exit 0 only if every validator's accuracy meets `validator_fixture_accuracy` in
  configs/eval_thresholds.yaml (1.00). Cases needing the local SNAP table are reported as skipped,
  never as passed.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    from app.mcp_server import config
    from app.rag import config as rag_config
    from app.validators import keys
    from app.validators.fixtures import run_all

    v = config.mcp()["validators"]
    parser = argparse.ArgumentParser(description="Run the deterministic-validator fixtures.")
    parser.add_argument("--index", type=Path, default=config.repo_path(v["fixtures_index"]))
    parser.add_argument("--keys-dir", type=Path, default=None)
    parser.add_argument("--report-dir", type=Path, default=ROOT / "evals" / "reports")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    index_path = args.index.resolve()
    if not index_path.is_file():
        print(f"error: fixture index not found: {args.index}", file=sys.stderr)
        return 1
    keys_dir = args.keys_dir or config.keys_dir()
    index = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    wanted = yaml.safe_load((index_path.parent / index["signatures"]["cases"]).read_text(encoding="utf-8"))["keys"]
    keys.generate(keys_dir, wanted)

    def cap(synthetic: bool) -> int:
        return rag_config.excerpt_limit("team_authored" if synthetic else "reference_only")

    report = run_all(index_path, schemas_dir=config.repo_path(v["schemas_dir"]), keystore=keys.Keystore(keys_dir),
                     tables_cfg=v["response_code_tables"], root=ROOT, excerpt_cap=cap, rules_version=v["rules_version"])
    gate = yaml.safe_load((ROOT / "configs" / "eval_thresholds.yaml").read_text(encoding="utf-8"))
    threshold = float(gate["gates"]["validator_fixture_accuracy"]["min"])

    print(f"{'validator':<16}{'passed':>7}{'failed':>7}{'skipped':>8}{'accuracy':>10}")
    ok = True
    for name, s in report["validators"].items():
        acc = s["accuracy"]
        ok &= acc is not None and acc >= threshold
        print(f"{name:<16}{s['passed']:>7}{s['failed']:>7}{s['skipped']:>8}{(f'{acc:.3f}' if acc is not None else '-'):>10}")
    for c in report["cases"]:
        if c["result"] == "failed":
            print(f"FAILED {c['validator']}/{c['id']}: expected {c['expected']}, got {c['actual']}")
    report.update({"threshold": threshold, "gate": "PASS" if ok else "FAIL",
                   "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    args.report_dir.mkdir(parents=True, exist_ok=True)
    out = args.report_dir / f"validators-{datetime.now().strftime('%Y-%m-%dT%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(f"validator_fixture_accuracy gate (>= {threshold:.2f}): {report['gate']}  report: {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
