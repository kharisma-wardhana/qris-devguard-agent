"""`vllm bench serve` result parser (p9-final spec Behaviour §2.5; AC #14-#15).

The benchmark runs inside the pod (the developer runs it; docs/p9-final-evaluation-packaging/runbook.md). This module
only reads its --save-result JSON. Key names were checked against vLLM 0.32.0 `vllm/benchmarks/serve.py`:
`mean_/median_/std_<metric>_ms`, and `p<N>_<metric>_ms` only for the percentiles passed in --metric-percentiles
(its default is "99", so the runbook passes "50,95"). The median comes from `median_*`, p95 from `p95_*`.
TTFT/TPOT are reported against the PRD §22 targets; they are not a release gate.
"""

from __future__ import annotations

from typing import Any

import yaml

from app.evaluation import ROOT, settings

REQUIRED = ("completed", "duration", "median_ttft_ms", "p95_ttft_ms", "median_tpot_ms", "p95_tpot_ms",
            "mean_ttft_ms", "mean_tpot_ms")
HARDWARE = ("gpu_sku", "driver", "cuda")


class BenchError(ValueError):
    """The result JSON lacks a field the report needs."""


def parse_result(result: dict[str, Any]) -> dict[str, Any]:
    missing = [k for k in REQUIRED if result.get(k) is None]
    if missing:
        raise BenchError(f"missing field(s) {', '.join(missing)}: run with --percentile-metrics ttft,tpot,itl "
                         "--metric-percentiles 50,95 --save-result")
    b = settings()["bench"]
    conc = result.get("max_concurrency")
    row = {"max_concurrency": conc, "num_prompts": result.get("num_prompts"), "completed": result["completed"],
           "failed": result.get("failed", 0), "duration_s": round(float(result["duration"]), 2),
           "ttft_ms": {"mean": result["mean_ttft_ms"], "p50": result["median_ttft_ms"], "p95": result["p95_ttft_ms"]},
           "tpot_ms": {"mean": result["mean_tpot_ms"], "p50": result["median_tpot_ms"], "p95": result["p95_tpot_ms"]},
           "output_throughput_tok_s": result.get("output_throughput"), "model_id": result.get("model_id"),
           "date": result.get("date")}
    notes = []
    if conc not in b["concurrencies"]:
        notes.append(f"max_concurrency {conc} is not one of the planned {b['concurrencies']}")
    if result.get("num_prompts") not in (None, b["num_prompts"]):
        notes.append(f"num_prompts {result.get('num_prompts')} differs from the planned {b['num_prompts']}")
    if result.get("failed"):
        notes.append(f"{result['failed']} request(s) failed")
    row["notes"] = notes
    row["gated_baseline"] = conc == b["gated_concurrency"]
    if row["gated_baseline"]:
        t = b["targets"]
        row["against_targets"] = {"ttft_p95_ms": {"target_max": t["ttft_p95_ms"], "met": row["ttft_ms"]["p95"] <= t["ttft_p95_ms"]},
                                  "tpot_p95_ms": {"target_max": t["tpot_p95_ms"], "met": row["tpot_ms"]["p95"] <= t["tpot_p95_ms"]}}
    return row


def model_identity() -> dict[str, Any]:
    m = yaml.safe_load((ROOT / "configs" / "model.yaml").read_text(encoding="utf-8"))
    return {"model": m["model"]["id"], "revision": m["model"]["revision"], "served_name": m["model"]["served_name"],
            "quantization": m["model"]["quantization"], "max_model_len": m["vllm"]["max_model_len"],
            "vllm_version": m["vllm"]["version"], "planned_gpu": m["gpu"]["sku"]}


def tunnel_ttft(healthcheck_lines: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Newest all-pass health check's chat TTFT through the tunnel (informational)."""
    for rec in reversed(healthcheck_lines):
        if rec.get("all_pass") and rec.get("ttft_ms") is not None:
            return {"ttft_ms": rec["ttft_ms"], "ts": rec.get("ts"), "gpu": rec.get("gpu")}
    return None


def build_report(rows: list[dict[str, Any]], hardware: dict[str, str | None],
                 healthcheck_lines: list[dict[str, Any]]) -> dict[str, Any]:
    missing = [k for k in HARDWARE if not hardware.get(k)]
    if missing:
        raise BenchError(f"hardware field(s) missing: {', '.join(missing)} (copy them from nvidia-smi in the pod)")
    ident = model_identity()
    notes = []
    sku = str(hardware["gpu_sku"])
    if "A5000" in sku.upper():
        notes.append("RTX A5000 has no native FP8: vLLM ran the FP8 checkpoint through a fallback kernel, so these "
                     "latencies are not the RTX PRO 4000 figures (docs/runpod-deployment.md).")
    if sku.replace(" ", "").upper() != ident["planned_gpu"].replace(" ", "").upper():
        notes.append(f"GPU differs from the planned {ident['planned_gpu']}.")
    b = settings()["bench"]
    return {"mode": "live", "hardware": hardware, "model": ident,
            "settings": {k: b[k] for k in ("dataset_name", "random_input_len", "random_output_len", "num_prompts",
                                           "seed", "concurrencies")},
            "rows": sorted(rows, key=lambda r: (r["max_concurrency"] is None, r["max_concurrency"] or 0)),
            "tunnel_ttft": tunnel_ttft(healthcheck_lines), "notes": notes, "gated": False}
