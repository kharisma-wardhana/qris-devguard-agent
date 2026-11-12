# Deploy, status and rollback: p9-demo-ui-alerts
*Author: Kharisma Wardhana. Stage: Deploy. Written: 2026-11-12. Via /aspec:make-it-simple-v2.*

These tools are documented only. Nothing here is wired to a credential or reaches anything beyond the developer's machine and its Podman network. The full procedure is in [runbook.md](runbook.md).

## This feature

P9 adds a local Streamlit demo UI (a client of `/api/v1`), four read routes, and a Langfuse alert webhook receiver that runs as the `alert-receiver` compose service. There is no schema migration and no new grant.

| Action | How | Who |
|---|---|---|
| Deploy (deps and env) | `uv sync --extra ui`; `uv run python scripts/init_env.py --add-missing` adds `LANGFUSE_ALERT_WEBHOOK_SECRET` with a random placeholder (compose needs the key) | Developer |
| Deploy (Langfuse) | `podman compose up -d --force-recreate langfuse-web langfuse-worker` picks up `LANGFUSE_WEBHOOK_WHITELISTED_HOST=alert-receiver`. In the Langfuse UI, add a Webhook action `http://alert-receiver/langfuse/alerts`, paste its secret into `.env` | Developer (Langfuse admin) |
| Deploy (receiver) | `podman compose --profile alerts up -d --force-recreate alert-receiver` | Developer |
| Deploy (run) | `uv run python scripts/mcp_server.py`, `uv run python scripts/api_server.py`, `uv run python scripts/ui_server.py` (all `127.0.0.1`) | Developer |
| Status | `GET /health/ready`; the UI sidebar's API status; `podman logs qris-devguard-agent_alert-receiver_1` (outcome, id, severity, size only); `uv run python scripts/send_test_alert.py --via-container qris-devguard-agent_langfuse-worker_1` should answer `{"stored": true}` | Developer |
| Rollback (code) | `git revert` the P9 commits. The P8 API returns, without the four GET routes | Human (repo owner) |
| Rollback (services and data) | `podman compose --profile alerts rm -sf alert-receiver`; delete the webhook automation in Langfuse; remove the whitelist line and recreate `langfuse-web`/`langfuse-worker`; delete `var/alerts/`. Local and synthetic only | Developer |

## Scoped MCP tools

The deploy/status/rollback tools proposed by earlier features still apply. P9 proposes two more, **documented, not wired**:

| Tool | Scope it would need | Never |
|---|---|---|
| `alert_feed_status` (read-only) | The count of stored alerts per severity and the newest alert's id, severity and received time, read through `app/alerts/store.read_alerts` (validated and redacted) | Return title or body text, write the store, acknowledge or act on an alert, or change Langfuse automations |
| `ui_health` (read-only) | Whether `127.0.0.1:<ui.port>/_stcore/health` answers `ok` | Start or stop the UI, mint a token, or read session state |

Minting demo tokens, recreating containers and editing the Langfuse webhook stay human actions. None of them may become an agent tool: an agent able to mint identities or repoint the webhook could act as an approver or silence the alert feed.
