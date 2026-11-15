"""Turn `vllm bench serve` results into the benchmark report (p9-final spec Behaviour §2.5; AC #14-#15).

Usage (after copying the result JSONs out of the pod into evals/reports/):
  uv run python scripts/bench_report.py evals/reports/bench-c1.json evals/reports/bench-c4.json \
      --gpu "RTX PRO 4000" --driver 580.95.05 --cuda 13.0

The hardware values come from `nvidia-smi` in the pod (runbook). Model identity comes from configs/model.yaml. The
tunnel TTFT is taken from the newest all-pass line of evals/reports/healthcheck.jsonl (informational only).
Writes evals/reports/bench-report-<ts>.json. Exit 0 on success, 1 on a missing field or unreadable input.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    from app.evaluation import bench, settings

    parser = argparse.ArgumentParser(description="Build the benchmark report from vllm bench serve results.")
    parser.add_argument("results", type=Path, nargs="+", help="vllm bench serve --save-result JSON files")
    parser.add_argument("--gpu", required=True, help="GPU SKU from nvidia-smi")
    parser.add_argument("--driver", required=True, help="driver version from nvidia-smi")
    parser.add_argument("--cuda", required=True, help="CUDA version from nvidia-smi")
    parser.add_argument("--vllm-image", default=None, help="vLLM image or wheel identifier, if known")
    parser.add_argument("--healthcheck", type=Path, default=ROOT / settings()["reports_dir"] / "healthcheck.jsonl")
    parser.add_argument("--report-dir", type=Path, default=ROOT / settings()["reports_dir"])
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    rows = []
    for path in args.results:
        try:
            rows.append(bench.parse_result(json.loads(path.read_text(encoding="utf-8"))))
        except bench.BenchError as exc:
            print(f"error: {path.name}: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"error: {path.name} unreadable: {type(exc).__name__}", file=sys.stderr)
            return 1
    lines = []
    if args.healthcheck.is_file():
        for ln in args.healthcheck.read_text(encoding="utf-8").splitlines():
            try:
                lines.append(json.loads(ln))
            except ValueError:
                continue
    try:
        report = bench.build_report(rows, {"gpu_sku": args.gpu, "driver": args.driver, "cuda": args.cuda,
                                           "vllm_image": args.vllm_image}, lines)
    except bench.BenchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    report["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report["source_files"] = [p.name for p in args.results]
    args.report_dir.mkdir(parents=True, exist_ok=True)
    out = args.report_dir / f"bench-report-{datetime.now().strftime('%Y-%m-%dT%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    for r in report["rows"]:
        print(f"concurrency {r['max_concurrency']}: TTFT p50 {r['ttft_ms']['p50']:.1f} / p95 {r['ttft_ms']['p95']:.1f} ms, "
              f"TPOT p50 {r['tpot_ms']['p50']:.1f} / p95 {r['tpot_ms']['p95']:.1f} ms"
              + ("  (baseline, reported against PRD §22 targets; not a gate)" if r["gated_baseline"] else ""))
    for n in report["notes"]:
        print(f"note: {n}")
    print(f"report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
