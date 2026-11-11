# Monitoring: p6-checkpoint-hitl
*Author: Kharisma Wardhana. Stage: Maintain. Written: 2026-11-11. Via /aspec:make-it-simple-v2.*

## Decision: no second control band

P6 reuses the project's single control band from P1 (vLLM `ttft_ms` from `evals/reports/healthcheck.jsonl`). Its tiers, producer, runbook and loop closure are in `docs/p1-infrastructure/monitoring.md` and are unchanged.

P6's new signals are **events to alert on**, not drifting measurements to band. They belong in P8's Langfuse alerts, or a small query over the P6 tables, rather than in `control_band.py`:

| Signal | Source | Why it's an alert, not a band |
|---|---|---|
| A refusal with `APPROVAL_MISMATCH`, `APPROVAL_INVALID` or `APPROVAL_NOT_USABLE` | MCP call log (`tool=create_support_ticket outcome=…`) | Every one is a possible bypass or replay attempt (T4). Even one deserves a look; a baseline would hide it. |
| A second ticket for one `idempotency_key` | `sandbox_tickets` | Impossible by the unique constraint. Seeing one means the schema was changed: page immediately. |
| Approvals expiring undecided | `approval_requests` (`status = 'expired'`) | A process signal (nobody is deciding), with too few events for 8-point windows in a sandbox. |
| Approvals pending past half their TTL | `approval_requests` | An operational nudge to approvers, not a quality metric. |

**When to revisit:** P8. Once Langfuse records spans and alerts, the refusal codes and the expired-approval count become alert rules. A band would only make sense for a continuous metric such as the decision-to-ticket latency, and only once real traffic exists.

## Verified on 2026-11-11

- `.claude/monitoring/control_band.py` is byte-identical to the skill reference, and `--self-check` passed.
- The weekly `security-scan` (`pip-audit` over `uv export --all-extras`) covers P6's new pins, `langgraph-checkpoint-postgres==3.2.0` and `psycopg-pool==3.3.3`, without a workflow change.

## Limits that still apply

- Only Western Electric rules 1–4 are implemented; rules 5–8 are not.
- The script cannot detect a baseline window that overlaps the recent window.
- 8 points is a floor, not a sound sample.
- The script is self-checked on synthetic data only. Pointing it at real data is a human action.
- **New in P6:** the database-backed tests that would catch a regression in the exactly-once rules do not run in CI (intake decision). Their safety net is the local `uv run pytest -m db` pasted in each PR (REVIEW.md).

## Closing the loop

The process is unchanged from P1:
1. A tier-2 or tier-3 finding, or a P6 alert above, produces an incident record.
2. That record's `## Follow-up intent` either links a new `intent.md`, which re-enters at Phase 1, or says plainly that no change is warranted.

P6 hands two open inputs forward:
- **Live flow 1** still does not cite SNAP §2.1.5. The recommended next step is deterministic source attachment (`agent-flow-report.md`, "Follow-up").
- **Per-tool database roles** (T13/§13.3) are P7.
