"""Export real Langfuse alert deliveries for the release gate (p9-final spec Architecture; AC #24, #25).

Usage (after the controlled alert test, with the alert-receiver's store on this machine):
  uv run python scripts/export_alerts.py

Reads the receiver's JSONL store (configs/agent.yaml alerts.store_path) through the API-side reader, which validates
every line and redacts titles and bodies. Synthetic test alerts from scripts/send_test_alert.py (id `synthetic-…`,
monitor `synthetic-…`) are excluded. Writes evals/reports/alerts-<ts>.json with the real deliveries (ID, monitor,
severity, times, title) and `real_alert_fired`: true only when at least one real delivery has severity ALERT.

Exit 0 when a real ALERT delivery exists; 1 otherwise (the report is written either way).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def is_synthetic(rec: dict[str, Any]) -> bool:
    return str(rec.get("id", "")).startswith("synthetic-") or str(rec.get("monitor_id", "")).startswith("synthetic")


def main(argv: list[str] | None = None) -> int:
    from app.alerts import store

    parser = argparse.ArgumentParser(description="Export real (non-synthetic) alert deliveries.")
    parser.add_argument("--store", type=Path, default=None, help="default: configs/agent.yaml alerts.store_path")
    parser.add_argument("--report-dir", type=Path, default=ROOT / "evals" / "reports")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    records, skipped = store.read_alerts(args.store or store.store_path(), limit=100_000)
    real = [r for r in records if not is_synthetic(r)]
    fired = any(r["severity"] == "ALERT" for r in real)
    report = {"mode": "live", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "store_lines_skipped": skipped, "synthetic_excluded": len(records) - len(real),
              "real_alert_fired": fired,
              "real": [{k: r.get(k) for k in ("id", "monitor_id", "severity", "title", "alert_timestamp", "received_at")}
                       for r in real]}
    args.report_dir.mkdir(parents=True, exist_ok=True)
    out = args.report_dir / f"alerts-{datetime.now().strftime('%Y-%m-%dT%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(real)} real deliveries ({sum(r['severity'] == 'ALERT' for r in real)} ALERT), "
          f"{report['synthetic_excluded']} synthetic excluded, {skipped} invalid line(s) skipped; "
          f"real_alert_fired={fired}  report: {out}")
    return 0 if fired else 1


if __name__ == "__main__":
    sys.exit(main())
