# Deploy, status and rollback: p8-memory-observability
*Author: Kharisma Wardhana. Stage: Deploy. Written: 2026-11-12. Via /aspec:make-it-simple-v2.*

These tools are documented only. Nothing here is wired to a credential or reaches anything beyond the
developer's machine and its local services (PostgreSQL, Qdrant, Langfuse on loopback).

## This feature

P8 adds migration `0003` (long-term memory and its history) with `devguard_app` grants, Langfuse tracing in the
API and the MCP server, deterministic scores, answer-echo fencing (prompt `compose-v2`), and an owner-run
retention command.

| Action | How | Who |
|---|---|---|
| Deploy (schema) | `uv run python scripts/db_migrate.py` applies `0003` and re-applies the role grants (re-runnable; applied locally on 2026-11-12) | Developer |
| Deploy (tracing) | Real `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` and `LANGFUSE_HOST` in `.env`, Langfuse running (`podman compose up -d`); restart `scripts/mcp_server.py` and `scripts/api_server.py`. Each prints `tracing on` or `tracing off` | Developer (Langfuse project keys) |
| Deploy (alerts) | The two alerts in `alerts.md`, created by hand in the Langfuse UI. **Deferred**: not created yet | Developer (Langfuse admin) |
| Status | Server start-up line (`tracing on/off`); traces in Langfuse filtered by environment `capstone`; `uv run python scripts/purge_retention.py --dry-run` (counts only); `uv run python scripts/scan_markers.py` (fencing false-positive count) | Developer |
| Rollback (tracing) | `observability.enabled: false` in `configs/agent.yaml`, or `DEVGUARD_TRACING=0`, then restart the servers. No revert needed | Developer |
| Rollback (code) | `git revert` the P8 commits. The prompt returns to `compose-v1`; the memory routes disappear | Human (repo owner) |
| Rollback (schema) | As the owner: `DROP TABLE long_term_memory_history, long_term_memory;` (the `app` grants go with the tables). `0003` adds tables only, so no data migration back | Human (repo owner) |
| Rollback (traces) | Langfuse keeps traces until its volumes are removed (retention policies need an Enterprise licence). Traces hold no content by construction | Developer |

Running `scripts/purge_retention.py` without `--dry-run` deletes stored data. The `deploy-gate` hook asks before it
runs, as it does for `purge_audit.py`.

## Scoped MCP tools

The deploy/status/rollback tools proposed by earlier features still apply. P8 proposes two more, **documented,
not wired**:

| Tool | Scope it would need | Never |
|---|---|---|
| `retention_status` (read-only) | The per-table counts of `purge_retention.py --dry-run`, run as the owner role inside a transaction that is always rolled back | Delete anything, return row content, or print a DSN |
| `tracing_status` (read-only) | Whether the API and MCP server started with tracing on, and the age of the newest trace for environment `capstone`, read with a read-only Langfuse key | Return span attributes, scores or user IDs, change alerts, or hold the project's write keys |

Running the real retention purge, creating or editing Langfuse alerts, and approving team memory patterns stay
human actions. None of them may become an agent tool: an agent able to purge could destroy audit-relevant data,
one able to edit alerts could silence them, and one able to approve patterns would remove the four-eyes control.

## CI judgement jobs (triage, changelog drafting)

**Not added.** A non-interactive LLM job would need a model API key as a CI secret, and `REVIEW.md` § Security
says workflows reference no secrets. Whether to allow one secret for such a job is for the repo owner to decide
(`REVIEW.md` § What a human must decide).
