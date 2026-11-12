# Intent: A local Streamlit demo UI drives chat, threads and approvals through /api/v1, and shows Langfuse alerts received by a loopback webhook
*Author: Kharisma Wardhana. Stage: Plan. Written: 2026-11-12. Via /aspec:make-it-simple-v2.*

## Problem statement

The agent works end to end (P5–P8), but you can only show it through curl, the scripts or Swagger. For the capstone demo and review, that means:

- **No one can watch a diagnosis happen.** There is no screen for a chat, a thread's message history, the §5 response contract (Summary, Findings, Validation results, Next steps, Sources, Confidence/limitations), or a proposed ticket waiting for approval. The PRD expects a "Web UI or API client" in front of the gateway (TRD architecture diagram) and an "approval UI/API" (P6 row, §27). Only the API half exists.
- **Thread handling is manual.** You create a thread with `POST /api/v1/threads` and must copy its ID by hand to continue it. No route lists a user's threads.
- **Alerts fire where no one sees them.** P8 configures `devguard-agent-p95-latency-high` and `devguard-incident-event` in the local Langfuse. Nothing in the demo receives an alert notification or shows that one fired. Langfuse alerts send notifications through a channel, and the only local channel is a webhook into a receiver we own.

## Proposed outcome

Run one local Streamlit app to do all of the following:

- **Pick a demo identity:** a synthetic subject with the `developer` or `approver` role. The UI gets a short-lived API token for it.
- **Threads:** create a new thread, see the threads that identity created, open one, and read its message history.
- **Chat:** send a message on the open thread and see the answer in the §5 contract shape, with its sources (citations) and validation results.
- **Approval:** when the agent proposes a sandbox ticket, see the pending approval. As the approver, approve or reject it and resume the thread, then see the outcome (ticket created or not).
- **Alerts:** a local webhook endpoint receives Langfuse alert notifications, checks and stores them, and the UI lists them: alert name, state, time, and a link to the Langfuse trace or alert where Langfuse provides one.

This is part of PRD P9 (final evaluation and packaging): demo screens and alert evidence. It is not all of P9. The release-gate run and the final report stay separate.

## Affected users and systems

- **Kharisma Wardhana:** runs the demo, captures screenshots, and acts as both the developer and the approver.
- **Capstone reviewers:** watch the demo. They aren't users of the system, and no real users exist.
- **New Streamlit app:** the demo UI. It calls `/api/v1` only.
- **`app/api/`:** probably a thread-listing route for the current subject, and the webhook receiver, if it lives in the API rather than as its own small server.
- **`app/db/`:** probably a table for received alerts, and possibly a query for a subject's threads.
- **Local Langfuse (`docker-compose.yml`):** its alerts get a webhook channel that points at the receiver.
- **P8 step 10:** the alert controlled test can use this receiver as its channel once P9 lands. P8's own chain is not changed by P9.
- **`configs/`, `pyproject.toml`, `docs/tech.md`, `docs/structure.md`:** Streamlit dependency, ports and settings, documented.

## Constraints

- **Capstone demo and review only.** It runs on the developer's machine with synthetic data and no real users. This is not a product UI.
- **Loopback only.** The UI, the API and the webhook receiver bind to `127.0.0.1`. If the Langfuse container can't reach a loopback receiver, work stops and the developer decides. A non-loopback bind is never chosen silently (same rule as P8).
- **The UI talks to `/api/v1` only.** It never reaches the database, the MCP server or vLLM directly, so the API's auth, rate limits, redaction and approval checks stay in the path.
- **No secrets anywhere.** No secret is shown in the UI, stored with an alert, or written to logs. Tokens are short-lived, and the signing secret stays in `.env`.
- **The webhook payload is untrusted.** Its size is capped and its schema validated. Its signature is checked if Langfuse signs it. It never expands any authority and never triggers an agent action.
- **Out of scope (user decision):** memory preferences, memory patterns and feedback.
- **Must not break:** existing P2–P8 behaviour and all existing tests.

## Open questions

- **Deadline:** does P9 have its own date, or does it share the 2026-11-20 overall deadline that P8 named?
- **How the UI gets a token:** mint it in-process from `MCP_AUTH_SECRET`/the API's signing key for a chosen synthetic subject (as the eval drivers do), or paste a token minted with `scripts/mint_dev_token.py`?
- **Thread listing:** add `GET /api/v1/threads` scoped to the caller, or keep the list of thread IDs in the UI session only?
- **Langfuse webhook format:** what Langfuse 4.76.0 sends (payload fields, signing header and secret), and whether it can be pointed at a loopback receiver from inside its container.
- **Where the receiver runs:** a route in the existing API (`/api/v1/...`), or a separate small receiver process. Either way the UI reads alerts through the API.
- **How alerts reach the UI:** polling on refresh is enough for a demo. Confirm that nothing push-based is needed.
