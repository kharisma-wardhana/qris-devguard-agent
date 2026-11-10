# Monitoring: p4-deterministic-mcp-tools
*Author: Kharisma Wardhana. Stage: Maintain. Written: 2026-11-10. Via /aspec:make-it-simple-v2.*

## Decision: no second control band

P4 reuses the project's single control band from P1, which watches `ttft_ms` from `evals/reports/healthcheck.jsonl`. Its tiers, producer, runbook and loop closure are in `docs/p1-infrastructure/monitoring.md` and are unchanged.

Two P4 signals were considered and are **not** banded:

- **Validator fixture accuracy is a gate, not a drifting metric.** It must be exactly 100% on every change (`validator_fixture_accuracy`). Any miss is a deterministic failure: `scripts/eval_validators.py` exits 1 and the CI test suite goes red. A control band would only blur that into "unusual".
- **MCP tool latency has no live series yet.** The server is local, and nothing calls it in a request path until P5's `execute_read_tools`. The only timings are one smoke test, including a 29.6 s cold search while BGE-M3 loaded on CPU. That is not a baseline.

**When to revisit:** once P5 routes agent requests through the MCP tools, per-call `ms` from the server's call log becomes a live metric. P5 can then propose a band for one tool at a time, starting with `search_qris_spec` because it carries the embedding (and possibly reranking) cost. The band would need its own producer, which parses the `tool=… outcome=… ms=…` log lines, never arguments, and a baseline window of at least 8 warm calls. The `INTERNAL_ERROR` and `SEARCH_UNAVAILABLE` outcome counts are better watched as alerts in P8 (Langfuse) than as a control band.

## Verified on 2026-11-10

- `.claude/monitoring/control_band.py` is byte-identical to the skill reference, and `--self-check` passed.
- The weekly `security-scan` workflow audits every package in `uv.lock`, extras included. It therefore covers P4's new direct pins (`mcp==2.3.0`, `pyjwt==2.15.1`, `cryptography==50.0.2`) and the SDK's transitive packages (Starlette, uvicorn, `httpx2`). No workflow change is needed.

## Limits that still apply

- Only Western Electric rules 1–4 are implemented; rules 5–8 are not.
- The script cannot detect a baseline window that overlaps the recent window.
- 8 points is a floor, not a sound sample.
- The script is self-checked on synthetic data only. Pointing it at real data is a human action.

## Closing the loop

The process is unchanged from P1:
1. A tier-2 or tier-3 finding produces an incident record.
2. That record's `## Follow-up intent` either links a new `intent.md`, which re-enters at Phase 1, or says plainly that no change is warranted.

P4's own results already give P5 these inputs:
- the cold-search cost, and whether to warm the embedder at server start;
- the filtered-search ranks, which keep their pool positions;
- the retrieval miss on the duplicate-reference query (`validator-report.md`).
