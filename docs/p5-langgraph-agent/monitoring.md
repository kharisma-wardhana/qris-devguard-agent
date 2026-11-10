# Monitoring: p5-langgraph-agent
*Author: Kharisma Wardhana. Stage: Maintain. Written: 2026-11-10. Via /aspec:make-it-simple-v2.*

## Decision: no second control band yet

P5 reuses the project's single control band from P1, which watches vLLM `ttft_ms` from `evals/reports/healthcheck.jsonl`. Its tiers, producer, runbook and loop closure are in `docs/p1-infrastructure/monitoring.md` and are unchanged. That band already covers the part of P5 most likely to drift, the model endpoint.

Agent signals were considered and are **not** banded now:

- **There is no live series yet.** The agent runs only while the user has a pod up. The one live session gave four flow runs (`agent-flow-report.md`), and a band needs at least 8 baseline points from comparable conditions (same GPU, warm embedder).
- **The candidate metric, once one exists:** `compose_diagnosis` latency per request, from the agent trace (`trace.llm.ms`). It isolates model time, which was about 90% of a warm request in the live run.
  - The source should be the P8 Langfuse spans, not a new log parser.
  - Total request latency is a poor metric, because a cold first search alone costs about 17–30 s.
- **Outcome rates are alerts, not bands.** Better watched as P8 Langfuse alerts:
  - the fallback rate (`evidence_flags.fallback`);
  - policy abstentions (rule 8);
  - `TOOL_UNAVAILABLE` outcomes.

**When to revisit:** in P8, once Langfuse records per-run spans. That phase can propose a band on `compose_diagnosis` latency per GPU type, with its own producer, a baseline window of at least 8 warm runs, and tiers.

## Verified on 2026-11-10

- `.claude/monitoring/control_band.py` is byte-identical to the skill reference, and `--self-check` passed.
- The weekly `security-scan` (`pip-audit` over `uv export --all-extras`) covers P5's new pins without a workflow change: `langgraph==1.2.20`, `fastapi==0.162.2`, `openai==3.49.0`, and their transitive packages (langchain-core, langgraph-checkpoint and others).

## Limits that still apply

- Only Western Electric rules 1–4 are implemented; rules 5–8 are not.
- The script cannot detect a baseline window that overlaps the recent window.
- 8 points is a floor, not a sound sample.
- The script is self-checked on synthetic data only. Pointing it at real data is a human action.

## Closing the loop

The process is unchanged from P1:
1. A tier-2 or tier-3 finding produces an incident record.
2. That record's `## Follow-up intent` either links a new `intent.md`, which re-enters at Phase 1, or says plainly that no change is warranted.

P5's measured result already gives P6 a follow-up intent input: live flow 1 must cite SNAP §2.1.5 (`agent-flow-report.md`, "Follow-up for P6").
