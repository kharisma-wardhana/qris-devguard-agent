# Alerts in the local Langfuse (P8 step 10)
*Author: Kharisma Wardhana. Stage: Build. Written: 2026-11-12. Via /aspec:make-it-simple-v2.*
*Implements: docs/p8-memory-observability/spec.md (Behaviour §5; AC #25)*

Two alerts are configured by hand in the local Langfuse UI (v4.76.0, OSS, `http://127.0.0.1:3000`). The
developer creates them with the admin login; the agent never reads that login or the project keys. Everything
below that says *as set* is filled in from what the UI actually offered, not from this plan.

## 1. `devguard-agent-p95-latency-high` (mandatory, TRD §21.3)

| Setting | Intended | As set |
|---|---|---|
| Source | Observations | _to record_ |
| Metric | p95 latency | _to record_ |
| Filter | environment `capstone`; observation name `agent-run` (the root span of a run), else tag `service:agent` | _to record_ |
| Window | the shortest Langfuse offers | _to record_ |
| Threshold | **3,000 ms**: above the measured warm baseline, below the delayed runs (see below) | _to record_ |
| Channel | whatever the UI requires; loopback only (see Stop rule) | _to record_ |

**Why name `agent-run`.** A tag filter alone covers every observation of a trace, including node spans of a
few milliseconds, which pull the p95 down. The root span's latency is the run's latency.

**Measured baseline (2026-11-12, API on the fake LLM, local MCP server and Qdrant):** 5 runs. The first took
52.1 s (cold start: the MCP server loads the CPU embedding model on its first search); the four warm runs took
1.2–1.6 s from the client. The threshold is set against the warm runs, and the controlled test starts after the
cold start has left the window.

## 2. `devguard-incident-event` (single events)

| Setting | Intended | As set |
|---|---|---|
| Source | Scores, boolean `incident_event` | _to record_ |
| Metric | count | _to record_ |
| Condition | at least 1 in the window | _to record_ |
| Window | the shortest Langfuse offers | _to record_ |
| Channel | as for alert 1 | _to record_ |

`incident_event=1` is sent by the agent for any MCP outcome `APPROVAL_*`, `AUDIT_UNAVAILABLE` or `INTERNAL_ERROR`,
and by the API crash handler (`api-error` trace), with the code as the score comment.

## Controlled test

All local: API with `--fake-llm` (`DEVGUARD_TEST_BACKENDS=1`), the MCP server, Postgres, Qdrant and Langfuse on
loopback. No pod. The driver mints short-lived tokens in-process for synthetic subjects and prints run IDs,
statuses and timings only.

1. **Latency.** Restart the API with `DEVGUARD_TEST_DELAY_MS=5000` (the cap). Every run then waits 5 s inside
   the `classify_intent` span, so a run takes about 6–7 s against a 3 s threshold. Send enough runs to fill the
   window, then wait for Langfuse's alert evaluation. Restart the API without the delay afterwards.
2. **Incident.** One escalation on a thread; the stored approval action is changed after it was proposed
   (owner role, local database), and a different subject approves. The ticket tool refuses the changed action
   with an `APPROVAL_*` outcome, the run sends `incident_event=1`, and no ticket is created.

**Stop rule (spec Flagged concerns).** If Langfuse requires a notification channel that can only be received
off loopback, the test stops and the developer decides. A non-loopback bind is never chosen silently.

## Evidence

Run IDs and their seeded trace IDs (`trace_id_for(run_id)`), the alert states and screenshots are recorded in
`observability-report.md`. Measured results only.
