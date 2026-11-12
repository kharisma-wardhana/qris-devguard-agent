# Runbook: demo UI and Langfuse alert webhook
*Author: Kharisma Wardhana. Stage: Build. Written: 2026-11-12. Via /aspec:make-it-simple-v2.*
*Implements: docs/p9-demo-ui-alerts/spec.md (Behaviour §7; AC #22)*

Local, capstone demo only. Synthetic data. Everything binds `127.0.0.1` or stays inside the Podman compose network.

**Demo-only.** The UI holds the token-signing secret (`MCP_AUTH_SECRET`) and mints 15-minute tokens for the synthetic identities in `configs/agent.yaml` `ui.identities`. Anyone who can run the UI can act as any of them. Never run it anywhere but the developer's machine.

## Once

```
uv sync --extra ui                                   # Streamlit
uv run python scripts/init_env.py --add-missing      # adds LANGFUSE_ALERT_WEBHOOK_SECRET (random placeholder) if missing
```

`docker-compose.yml` reads `LANGFUSE_ALERT_WEBHOOK_SECRET` with `:?`, so every `podman compose` command fails until `.env` has it.

## Start order

1. **Stack:**
   - `podman compose up -d`
   - `podman compose up -d --force-recreate langfuse-web langfuse-worker`, the first time after pulling P9, so Langfuse picks up `LANGFUSE_WEBHOOK_WHITELISTED_HOST=alert-receiver`.
2. **Alert receiver:** `podman compose --profile alerts up -d alert-receiver`. Its log says `alert receiver listening bind=all port=80 path=/langfuse/alerts`.
3. **MCP server:** `uv run python scripts/mcp_server.py`
4. **Model:** `bash scripts/tunnel.sh` for the real model. Or, for a dry run without the pod: `DEVGUARD_TEST_BACKENDS=1 uv run python scripts/api_server.py --fake-llm`.
5. **API:** `uv run python scripts/api_server.py`
6. **UI:** `uv run python scripts/ui_server.py`, then open `http://127.0.0.1:8501`.

## Langfuse webhook (once per Langfuse instance)

1. In Langfuse (`http://127.0.0.1:3000`, admin login), open the alert (for example `devguard-incident-event`) and add a **Webhook** action with the URL `http://alert-receiver/langfuse/alerts`. Langfuse 4.76.0 accepted this `http://` URL on 2026-11-12.
2. Copy the signing secret it shows over `LANGFUSE_ALERT_WEBHOOK_SECRET` in `.env`.
3. Run `podman compose --profile alerts up -d --force-recreate alert-receiver`.
4. Check the path end to end with a synthetic alert:
   ```
   uv run python scripts/send_test_alert.py --via-container qris-devguard-agent_langfuse-worker_1
   ```
   The answer should be `{"stored": true}`. A `401` means the secret in `.env` doesn't match the running receiver.

## Demo script

1. **Developer (demo-dev):** click **New thread** and pick the example "Signature rejected (synthetic RSA fixture)". Send it, and show the §5 sections and the validator verdict.
2. **Same thread:** pick "Escalate to a sandbox ticket (needs approval)". The thread shows the pending approval, and the chat form is disabled.
3. **Switch to Approver (demo-approver) → Approvals:** the approval shows with its sanitized draft. Click **Approve**, and the page shows the sandbox ticket ID. Optionally, reject a second one: "nothing was created".
4. **Developer → Approvals → Decide by approval ID:** the attempt gets `code: FORBIDDEN` (the four-eyes rule and role check).
5. **Approver → Alerts:** turn on **Auto-refresh**. Fire the alert (below). A toast appears and the alert is listed with its severity, window and an **Open in Langfuse** button.

## Controlled alert test

- **`devguard-incident-event`:** produce one `incident_event` (P8 `alerts.md`, controlled test 2: an approval whose stored action was changed). Langfuse notifies on a state change (OK/UNKNOWN → ALERT) only; the worker's `MonitorRunner` evaluates every 30 s.
- **p95 latency:** restart the API with `DEVGUARD_TEST_DELAY_MS=5000` and send enough runs to fill the window.
- **What to record:** the alert ID (not `synthetic-…`), its time and a screenshot, in `demo-report.md` AC #21.

## Manual browser pass (once per release)

AppTest drives the page without a browser (`tests/integration/test_ui_e2e.py`). Once per release, run the demo script in a real browser and check:
- no `Authorization` value appears in the page or the URL;
- a planted markdown link in a message stays literal text;
- the page works at a narrow window width.

## Stop

- Stop the UI with `Ctrl+C` in its terminal (Streamlit runs in the launcher's own process).
- Stop the receiver with `podman compose --profile alerts stop alert-receiver`.
- `var/alerts/alerts.jsonl` keeps at most 500 alerts. Delete it to start empty.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `RATE_LIMITED` on the page | More than 20 quick reads for one identity. Reads are cached for `ui.read_cache_seconds`; wait for `Retry-After`. |
| Receiver log shows `outcome=signature_invalid` | `.env` secret differs from the Langfuse webhook secret, or the receiver wasn't recreated after pasting it. |
| Receiver logs nothing after an alert should have fired | The alert didn't change state. Check `podman logs qris-devguard-agent_langfuse-worker_1` for `MonitorRunner ... published N events`. |
| `SERVICE_UNAVAILABLE` on Threads | PostgreSQL isn't up or migrated: `uv run python scripts/db_migrate.py`. |
