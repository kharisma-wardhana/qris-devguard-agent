# Monitoring: p3-reranking-eval
*Author: Kharisma Wardhana. Stage: Maintain. Written: 2026-11-10. Via /aspec:make-it-simple-v2.*

## Decision: no second control band

P3 reuses the project's single control band from P1. That band watches `ttft_ms` from `evals/reports/healthcheck.jsonl`. Its tiers, producer, runbook and loop closure are in `docs/p1-infrastructure/monitoring.md` and are unchanged.

Rerank latency is **not** banded yet:
- **Reranking is in no request path.** P5 decides whether reranking runs on CPU, on the GPU pod, or not at all (see `rerank-report.md`). A baseline taken now would describe a configuration that may never ship.
- **The only data is one eval run.** It has 50 per-query timings, including two outliers (3,223 s and 2,563 s) that were not explained. That is one batch measurement, not a time series of live behaviour.

**When to revisit:** once P5 puts reranking into `search_qris_spec`, its per-request latency becomes a live metric. P5 can then propose a band for it, with its own producer, a baseline window of at least 8 points, and tiers.

## Verified on 2026-11-10

- `.claude/monitoring/control_band.py` is byte-identical to the skill reference, and `--self-check` passed 25 of 25 checks.
- The weekly `security-scan` (`pip-audit --all-extras`) already covers the reranker's stack (sentence-transformers, torch). P3 added no dependencies.

## Limits that still apply

- Only Western Electric rules 1–4 are implemented; rules 5–8 are not.
- The script cannot detect a baseline window that overlaps the recent window.
- 8 points is a floor, not a sound sample.
- The script is self-checked on synthetic data only. Pointing it at real data is a human action.

## Closing the loop

The process is unchanged from P1:
1. A tier-2 or tier-3 finding produces an incident record.
2. That record's `## Follow-up intent` either links a new `intent.md`, which re-enters at Phase 1, or says plainly that no change is warranted.

P3's own measured results already create follow-up work for P5: the rerank latency, and whether to rerank at all.
