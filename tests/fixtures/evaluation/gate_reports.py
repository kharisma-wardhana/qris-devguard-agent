"""Builds a full, passing set of SYNTHETIC evidence reports for the release-gate tests (p9-final plan step 7).

Shapes follow the real producers: retrieval (P3 eval_retrieval), validators, security (eval_security), agent flows,
answers (eval_answers), judge (judge_answers), crash-resume, alerts and bench-report. Dataset versions match the
committed dataset files, so a fresh set grades PASS on every report-backed gate. Numbers are invented.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

WHEN = "2026-11-18T10:00:00+00:00"
STAMP = "2026-11-18T100000"


def reports(when: str = WHEN) -> dict[str, dict[str, Any]]:
    sec_ids = ["S01", "S02", "S11", "S12", "S13", "S14", "G-SECRETS"]
    return {
        f"retrieval-{STAMP}.json": {"measured_at": when, "dataset_version": "0.2.0", "before_after": {
            "original": {"candidate_recall@20": 1.0, "reranked_recall@5": 0.9},
            "all": {"candidate_recall@20": 0.95, "reranked_recall@5": 0.9}}},
        f"validators-{STAMP}.json": {"fixture_set_version": "0.1.0", "generated_at": when, "validators": {
            "payload": {"accuracy": 1.0}, "signature": {"accuracy": 1.0}, "response_codes": {"accuracy": 1.0}}},
        f"security-{STAMP}.json": {"generated_at": STAMP, "dataset_version": 1,
                                   "results": [{"id": i, "status": "PASS"} for i in sec_ids]},
        f"agent-flows-live-{STAMP}.json": {"mode": "live", "dataset_version": "0.1.0", "generated_at": when,
                                           "flows": [{"id": f"flow-{n}", "passed": True, "failures": []} for n in range(4)]
                                           + [{"id": "flow-4b", "passed": None, "skipped": "not run in live mode"}]},
        f"answers-live-{STAMP}.json": {"mode": "live", "dataset_version": "1.0.0", "generated_at": when,
                                       "summary": {"citation_validity": 1.0, "fabricated_crypto": 0, "secret_findings": 0}},
        f"judge-{STAMP}.json": {"mode": "live", "dataset_version": "1.0.0", "generated_at": when, "fake_client": False,
                                "model": "claude-opus-5-5", "prompt_version": "judge-v1",
                                "metrics": {"golden_answer_supported": 0.93, "citation_support_precision": 0.97,
                                            "not_judged": 0},
                                "calibration": {"status": "measured", "percent_agreement": 0.9, "cohens_kappa": 0.78}},
        f"crash-resume-{STAMP}.json": {"generated_at": when, "status": "PASS"},
        f"alerts-{STAMP}.json": {"mode": "live", "generated_at": when, "real_alert_fired": True,
                                 "real": [{"id": "evt_1", "severity": "ALERT"}]},
        f"bench-report-{STAMP}.json": {"mode": "live", "generated_at": when,
                                       "hardware": {"gpu_sku": "RTX PRO 4000", "driver": "580", "cuda": "13.0"},
                                       "rows": [{"max_concurrency": 1, "ttft_ms": {"p50": 200.0, "p95": 260.0},
                                                 "tpot_ms": {"p50": 30.0, "p95": 32.0},
                                                 "against_targets": {"ttft_p95_ms": {"met": True},
                                                                     "tpot_p95_ms": {"met": True}}}],
                                       "notes": []},
    }


def write_all(directory: Path, when: str = WHEN, **overrides: Any) -> dict[str, Path]:
    """Write every report; `overrides` maps a filename prefix (e.g. "judge") to a dict merged into that report, or
    to None to leave the report out."""
    directory.mkdir(parents=True, exist_ok=True)
    known = {n.rsplit("-" + STAMP, 1)[0] for n in reports(when)}
    unknown = set(overrides) - known
    if unknown:
        raise KeyError(f"unknown report prefix(es) {sorted(unknown)}; known: {sorted(known)}")
    out = {}
    for name, data in reports(when).items():
        prefix = name.rsplit("-" + STAMP, 1)[0]
        if prefix in overrides:
            if overrides[prefix] is None:
                continue
            data = {**data, **overrides[prefix]}
        path = directory / name
        path.write_text(json.dumps(data), encoding="utf-8")
        out[prefix] = path
    return out
