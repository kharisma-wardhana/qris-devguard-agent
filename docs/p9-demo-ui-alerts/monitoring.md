# Monitoring: p9-demo-ui-alerts
*Author: Kharisma Wardhana. Stage: Maintain. Written: 2026-11-12. Via /aspec:make-it-simple-v2.*

## Decision: no new control band

The project's bands stay as they are: P1's vLLM `ttft_ms` (`docs/p1-infrastructure/monitoring.md`) and P7's daily MCP refusal share (`docs/p7-mcp-hardening/monitoring.md`). P9 adds no continuous metric worth a band:
- **The UI is a local demo client.** Its traffic is one presenter's clicks, already counted by the API's logs and the P7 refusal band.
- **The receiver is event-driven.** A refused delivery (`signature_invalid`, `signature_stale`) is a single event to look at, not a ratio to band. On this machine there are a handful of deliveries per demo, far below the 8-point floor, and a constant baseline has zero sigma (the script refuses it).

Single events stay **alerts, not bands**, as P6 to P8 decided. P9 is the place those alerts become visible: the Alerts page.

`.claude/monitoring/control_band.py` was compared with the skill reference on 2026-11-12: **byte-identical**, not re-copied. `--self-check` passed (every line `ok`, last line `self-check passed`).

## Limits (stated, not assumed)

- The existing detector implements Western Electric rules 1–4 only; rules 5–8 (stratification, mixtures, trends) are not implemented.
- It can't tell when a baseline window overlaps the recent window; the producer keeps them disjoint.
- 8 baseline points is a floor, not a sound sample.
- The detector has been self-checked on synthetic data only. Pointing a band at a real endpoint and scheduling it are the developer's actions.

## Alert feed checks (runbook entries)

| Check | How | Healthy |
|---|---|---|
| Receiver up | `podman ps --filter name=alert-receiver` | `Up`; no `ports` column |
| Path from Langfuse works | `uv run python scripts/send_test_alert.py --via-container qris-devguard-agent_langfuse-worker_1` | `{"stored": true}` (a new `synthetic-…` id) |
| Langfuse evaluates alerts | `podman logs --since 10m qris-devguard-agent_langfuse-worker_1 \| grep MonitorRunner` | a line every 30 s; `published N events` > 0 when an alert changes state |
| Deliveries are accepted | `podman logs qris-devguard-agent_alert-receiver_1` | `outcome=stored` / `duplicate`; no run of `signature_invalid` |
| Store readable | Approver → **Alerts** page, or `GET /api/v1/alerts` | `skipped: 0` |

## Response tiers (project-wide, unchanged)

| Tier | Meaning | For P9 |
|---|---|---|
| 0 | in control | Nothing. |
| 1 | log only | A single `signature_invalid` or `signature_stale` in the receiver log after a secret change: re-paste the secret and recreate `alert-receiver`. No agent involvement. |
| 2 | diagnose read-only | Repeated refused deliveries, `skipped > 0` on the Alerts page, or `MonitorRunner` silent. Claude may read the receiver and worker logs (no `.env`, no secret) and the store, and must write an incident record. Nothing is restarted or changed by the agent. |
| 3 | propose PR or runbook | A delivery that should have been refused was stored, a stored record failed re-validation, or token text appeared on a page. Claude proposes a fix as a PR, re-running the T20 tests; the developer merges. |

Tier-2 invocation (read-only; outputs go into the prompt as files, never interpolated):

```bash
podman logs --since 1h qris-devguard-agent_alert-receiver_1 > evals/reports/receiver.log 2>&1
podman logs --since 1h qris-devguard-agent_langfuse-worker_1 2>&1 | grep -E "MonitorRunner|webhook" > evals/reports/worker.log
claude -p "Read evals/reports/receiver.log and evals/reports/worker.log. Diagnose why Langfuse alerts are not stored. Read-only: do not restart, edit or read .env. Write docs/p9-demo-ui-alerts/incidents/<date>-<slug>.md from the incident template."
```

## Scheduled security scans (reused)

- **Dependencies:** `security-scan.yml` runs pip-audit weekly on `uv export --all-extras`, so `streamlit` and its dependencies are covered.
- **Containers:** `container-scan.yml` runs Trivy weekly on every compose image, including the receiver's `python:3.11.14-slim-bookworm` (added in P9 step 6).

No new scanner was added.

## Closing the loop

A tier-2 or tier-3 finding writes `docs/p9-demo-ui-alerts/incidents/YYYY-MM-DD-<slug>.md`. Its `## Follow-up intent` either links a new `intent.md`, which re-enters at Phase 1 as its own feature, or says plainly that no change is warranted (for example, "secret rotated, recreated, verified with send_test_alert").

Open item carried by this feature: **AC #21** (a real Langfuse alert shown) is deferred. It is recorded in `demo-report.md` and closed by recording evidence there, not by a new feature.
