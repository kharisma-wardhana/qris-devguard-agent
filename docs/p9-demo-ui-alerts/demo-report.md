# Demo report: Langfuse alert webhook and demo UI (P9 step 10)
*Author: Kharisma Wardhana. Stage: Build. Written: 2026-11-12. Via /aspec:make-it-simple-v2.*
*Implements: docs/p9-demo-ui-alerts/spec.md (AC #20, #21)*

Everything here was measured locally on the developer's machine: Podman 6.1, Langfuse 4.76.0 OSS, receiver on `python:3.11.14-slim-bookworm`. Only synthetic data was used. No pod or other non-local endpoint was involved.

## Reachability before the design (2026-11-12, spec Architecture)

| Probe | Result |
|---|---|
| Host listener on `127.0.0.1:18080`, curl from the host | `probe-ok` |
| Same listener, `wget http://host.containers.internal:18080/` from `langfuse-worker` | `host.containers.internal` resolves to a private `10.x` address; `wget` exit 4 (unreachable) |

Conclusion: a receiver bound to the host's loopback can't be reached from the Langfuse container. User decision A: a receiver container in the compose network.

## AC #20: signed synthetic alert from inside the Langfuse container (MET, 2026-11-12)

1. **Receiver up** (`podman compose --profile alerts up -d alert-receiver`). Its log: `alert receiver listening bind=all port=80 path=/langfuse/alerts`. It runs as uid 65534 with no host port.
2. **Unsigned POST** from `langfuse-worker` to `http://alert-receiver/langfuse/alerts`: `HTTP/1.0 401 Unauthorized`. Receiver log: `outcome=signature_missing size=2`.
3. **Signed synthetic alert**, `uv run python scripts/send_test_alert.py --via-container qris-devguard-agent_langfuse-worker_1`:
   ```
   alert id: synthetic-5dd5b4ee6d04  severity: ALERT
   receiver (from qris-devguard-agent_langfuse-worker_1 to http://alert-receiver/langfuse/alerts): {"stored": true}
   ```
   Receiver log: `outcome=stored id=synthetic-5dd5b4ee6d04 severity=ALERT size=575`. `var/alerts/alerts.jsonl` holds 1 line.
4. **API read-back.** The real `scripts/api_server.py` started on loopback, and `GET /api/v1/alerts` was called with a minted token:
   ```
   HTTP 200 skipped 0
   synthetic-5dd5b4ee6d04 ALERT 2026-11-12T04:39:11+00:00 | SYNTHETIC test alert (scripts/send_test_alert.py)
   developer: 403 FORBIDDEN
   ```

What this proves: the receiver can be reached from inside the Langfuse container on the path Langfuse uses, the signature check and the store work end to end, and the API returns the stored alert only to approver roles. It does **not** prove that Langfuse itself accepts `http://alert-receiver` as a webhook target (the SSRF whitelist and the possible HTTPS requirement). That is AC #21.

## AC #21: a real Langfuse alert shown on the Alerts page (PENDING: developer action)

Steps (the developer holds the Langfuse admin login; the agent doesn't):

1. Recreate the Langfuse containers so they pick up `LANGFUSE_WEBHOOK_WHITELISTED_HOST=alert-receiver`.
2. In the Langfuse UI, create a webhook automation for an alert with the URL `http://alert-receiver/langfuse/alerts`. Record whether the UI accepts an `http://` URL. If it refuses, stop (spec Flagged concerns).
3. Paste the webhook's signing secret over `LANGFUSE_ALERT_WEBHOOK_SECRET` in `.env`, then recreate `alert-receiver`.
4. Fire the alert with P8's controlled test (for example one `incident_event`), then open the UI's Alerts page as the approver.

| Item | Result |
|---|---|
| Langfuse accepted `http://alert-receiver/langfuse/alerts` | _to record_ |
| Alert ID and receive time | _to record_ |
| Shown on the Alerts page (screenshot) | _to record_ |
