# Spec: A local Streamlit demo UI drives chat, threads and approvals through /api/v1, and shows Langfuse alerts received by a loopback webhook
*Author: Kharisma Wardhana. Stage: Design. Written: 2026-11-12. Via /aspec:make-it-simple-v2.*
*Implements: docs/p9-demo-ui-alerts/intent.md*

## Behaviour

### 1. Demo identity

- The sidebar offers a fixed list of synthetic demo identities from config. By default these are `demo-dev` (role `developer`) and `demo-approver` (role `support_engineer`, an approver role in `configs/security.yaml`).
- When an identity is chosen, the UI mints a short-lived API-audience token for it in-process (15 minutes by default, re-minted before expiry), using the same signing secret as `scripts/mint_dev_token.py`.
- The token is kept in server-side session state only. It is never shown, logged, put in a URL, or sent anywhere but the API.
- The sidebar shows `/health/ready` (`mcp` and `llm` up or down) and the API base URL.

### 2. Threads

- **New thread:** calls `POST /api/v1/threads` and opens the new thread.
- **My threads:** a list of the current identity's threads from the new `GET /api/v1/threads`, newest first, showing each thread's ID (shortened), status and last update. Selecting one opens it.
- **Open thread:** shows the thread's status and its history from the new `GET /api/v1/threads/{id}/messages`: one entry per completed run, with the sanitized question and the answer.
  - A thread that is `awaiting_approval` shows its pending approval (see §4).
- Another identity's thread can't be opened. The API answers 404 exactly as for an unknown ID (existing rule, T11).

### 3. Chat

- A message box sends `POST /api/v1/threads/{id}/messages` on the open thread.
- An optional, collapsed "context" field takes a JSON object for the API's `context` blocks (`api_family`, `endpoint_id`, `payload`, `signature`, `response_code`). The API validates it; the UI does not.
- A short list of built-in synthetic example prompts fills the box for the demo. They use the existing synthetic fixtures only.
- The answer is shown in the §5 response contract order: Summary, Findings (confirmed / likely / unverified), Validation results, Next steps, Sources, Confidence/limitations, plus the run ID, intent and risk level.
- **All answer, question, source and alert text is shown as plain text.** Markdown and HTML in it are never rendered, so a link or markup planted in retrieved or user text can't become clickable or active (T3).
- An error shows the §18 envelope's `code`, `message` and `correlation_id`, and the `field_errors` field names and reasons, never submitted values. A 429 shows its `Retry-After`.

### 4. Approval

- When a message ends `awaiting_approval`, the thread view shows the approval: action, target, sanitized payload, evidence IDs, expected side effect, requester, expiry and status (the existing `approval_view`).
- An approver identity sees an **Approvals** page listing pending approvals from the new `GET /api/v1/approvals?status=pending`, newest first. Opening one shows the same view, with Approve and Reject buttons and an optional reason, which call the existing `POST /api/v1/approvals/{id}/decision`.
- The result is shown: the approval's new status, the resumed run's answer, and the ticket ID when one was created.
- The existing rules are unchanged and show through as errors:
  - A `developer` can't decide (403).
  - Nobody can approve their own action (403).
  - An expired approval gives `APPROVAL_EXPIRED` and nothing is created.
- A thread whose approval was decided but whose resume never finished shows a **Resume** button (the existing `POST /api/v1/threads/{id}/resume`).

### 5. Alert webhook receiver

- A small receiver runs as its own container in the compose network as `alert-receiver`, port 80. It has no port published on the host.
- Langfuse's alert automation uses the Webhook action with the URL `http://alert-receiver/langfuse/alerts`. Langfuse is told to trust only that hostname (`LANGFUSE_WEBHOOK_WHITELISTED_HOST=alert-receiver`).
- For each `POST /langfuse/alerts` the receiver:
  1. **Size check:** refuses a body over 64 KiB with 413, before reading the rest.
  2. **Signature check:** verifies `x-langfuse-signature` (`t=<ts>,v1=<hex>`, HMAC-SHA256 over `"<ts>.<raw body>"`, constant-time compare) with the webhook's signing secret. A missing, malformed or wrong signature gets 401, and nothing is stored.
  3. **Freshness check:** refuses a `t` more than 10 minutes from the receiver's clock with 401. This is our own replay window; Langfuse doesn't define one.
  4. **Payload check:** validates the JSON strictly against the documented shape (`type` must be `monitor-alert`, `apiVersion` must be `v1`, `payload.severity` must be one of the documented values). Anything else gets 422, and nothing is stored.
  5. **Store:** keeps only the allowlisted fields (see Data). Text fields are truncated. `permalink` is kept only if it starts with the configured Langfuse base URL.
  6. **Deduplicate:** the alert's `id` is stored once. A retried delivery answers 200 and isn't stored again.
  7. **Response:** answers 200 with `{"stored": true|false}` and nothing else.
- Every other path and method gets 404 or 405. The receiver logs one line per request: outcome, alert ID if valid, severity, size. It never logs the body or headers.
- **A received alert never triggers anything.** No agent run, no tool call, no outbound request.

### 6. Alerts in the API and UI

- The new route `GET /api/v1/alerts` (approver roles only; `developer` and `kb_maintainer` get 403) reads the receiver's store and re-validates every record. A record that fails validation is skipped and counted.
  - Before returning, it applies the existing redaction rules to text fields.
  - It returns the newest 50: alert ID, monitor ID, severity, title, body, window, from/to times, received time, and permalink (if kept).
- The UI's **Alerts** page polls this route on a Refresh button and, if switched on, every 15 seconds. It shows a severity badge (`ALERT`, `WARNING`, `OK`, …), the title, body and times, and the permalink as a link into the local Langfuse.
- When a refresh finds new `ALERT`-severity entries, a toast says so.
- If the store is missing or empty, the page says "No alerts received yet". If the receiver isn't running, the API still answers, with the alerts already stored.

### 7. Running it

- One launcher starts Streamlit on `127.0.0.1` only, with Streamlit's usage statistics off. It refuses to start if the configured host isn't loopback (same rule as the API).
- Its startup line says which API URL it uses.
- It needs the API running. The API needs the MCP server and either the vLLM tunnel or, for tests, the fake LLM.

## Architecture and the reason for it

- **Streamlit, as asked.** It runs as a separate local process and talks to the API over HTTP. The UI holds no agent, database or MCP client, so every request passes the API's existing auth, rate limits, body limit, redaction and approval checks. It is a client of the system, not a second way in.
- **The HTTP client is separate from the Streamlit pages.** All API calls and token minting go through a plain Python client module with no Streamlit import. That module can be unit-tested directly, and the pages stay thin. Pages are tested with Streamlit's own `AppTest` against a fake client.
- **The receiver is a container in the compose network, because a host listener can't be reached.**
  - Measured on 2026-11-12: from inside `langfuse-worker`, `host.containers.internal` resolves to a private `10.x` address on the Windows host, and a request to a listener bound to `127.0.0.1` failed.
  - Binding the host to that address would expose it to the LAN (rejected, user decision A).
  - Inside the compose network, the receiver listens on port 80, the only port besides 443 that Langfuse delivers to, and nothing new binds on the host.
- **The receiver is stdlib-only Python on a stock pinned `python:3.11-slim` image, with the script mounted read-only.**
  - There's no image build, no pip install and no new dependency in the receiver, so this machine's TLS interception doesn't matter.
  - Its attack surface is the standard library's HTTP server plus about a hundred lines of our code.
  - It runs as a non-root user. The image goes into the existing container scan.
- **A JSONL file on a bind mount connects the receiver and the API, not PostgreSQL.**
  - Writing to PostgreSQL from a stdlib-only container would need a driver (an image build) and a fourth least-privilege role. That costs a lot for a single-developer demo.
  - An append-only file the receiver alone writes and the API alone reads is enough at demo volume, and the API re-validates every line because the file is a trust boundary.
  - This departs from `docs/tech.md`'s "PostgreSQL for all durable application state". Alerts are demo evidence, not application state.
- **New API routes stay inside the existing patterns:** the §18 envelope, `other_identity` rate limit, owner-scoped queries with no existence leak, approver-role checks from `configs/security.yaml`, and the `devguard_app` role (`SELECT` on tables it can already read, so no new grant).
- **Thread history comes from the LangGraph checkpoint history.** Each completed run's sanitized `user_query` and `final_answer` are already in the checkpoints. No new table and no copy of message text is created.
- **Config:** the `ui` section (host, port, API URL, identities, token TTL, refresh interval) and the `alerts` section (store path, record cap, max age of `t`, Langfuse base URL for permalinks) go in `configs/*.yaml`, not in code (project convention).
- **Dependency:** `streamlit`, pinned, as a new optional extra `ui`, like `embed`. The API and MCP server don't need it. CI installs the extra so the UI tests run there instead of being skipped.
- **Conformance:** this follows `docs/structure.md` (an app package per concern, launchers in `scripts/`, configs in `configs/`) and adds one UI package beside `app/api/`. It contradicts no earlier feature's architecture decision. P8's "alerts in Langfuse's own alert feature, not in our code" still holds: Langfuse evaluates and fires the alert, and P9 only receives the notification.

## Data

- **Received alerts:**
  - **Where:** one JSONL file in a git-ignored local directory, bind-mounted read-write into the receiver. The API reads it.
  - **Shape:** one record per alert with these fields only:
    - alert `id`;
    - `received_at` (receiver clock);
    - `timestamp`, `fromTimestamp` and `toTimestamp` from the payload;
    - `monitorId`;
    - `severity`;
    - `message.title` (≤ 200 characters) and `message.body` (≤ 1,000 characters);
    - `window`;
    - `view`;
    - `permalink` (kept only under the Langfuse base URL).
  - **Never stored:** the raw body, headers, the signature, `filters` and `projectId`.
  - **Retention:** at most 500 records. Beyond that, the oldest are dropped when the file is next written. Synthetic, local, demo-only.
  - **Sensitivity:** low. Alert text comes from our own monitors over synthetic traces. It is still treated as untrusted, and redacted on read.
- **Webhook signing secret:**
  - Langfuse generates it when the developer creates the webhook. The developer copies it into `.env` (git-ignored) as one new key, which is passed to the receiver container's environment.
  - The agent never reads or prints it. `.env.example` gets the key name with no value.
- **No new database table, column or grant.** The two new reads (a caller's threads, pending approvals) use existing tables and indexes.
- **UI session state:** the chosen identity, the short-lived token, the open thread ID and the last responses, held in the Streamlit server process memory only and gone when the session ends. Nothing is written to disk by the UI.

## Acceptance criteria

**API**

1. `GET /api/v1/threads` returns only the caller's threads (newest `updated_at` first, at most 50), each with `thread_id`, `status`, `created_at` and `updated_at`. Another subject's threads never appear. Without a token it returns 401, and a burst over the `api_other` bucket gets 429.
2. `GET /api/v1/threads/{id}/messages` returns, for the owner, one entry per completed run in order, with `run_id`, the sanitized question and the answer. A secret sent in a message appears only in redacted form. Another subject's thread or an unknown ID returns 404 with the same body shape.
3. `GET /api/v1/approvals?status=pending` returns pending approvals in the `approval_view` shape for `support_engineer` and `admin`. It returns 403 for `developer` and `kb_maintainer`, and 422 for any other `status` value.
4. `GET /api/v1/alerts` returns stored alerts newest first (at most 50), with the fields in Data, for approver roles only (403 otherwise).
   - A malformed or schema-invalid line in the store is skipped, not returned, and doesn't fail the request.
   - A secret-shaped value planted in a stored alert's body is returned redacted.
   - A missing store file returns an empty list.
5. All existing tests still pass unchanged. No existing route changes its response.

**Receiver**

6. A correctly signed, fresh, valid `monitor-alert` POST is stored once and answers 200. The same `id` sent again answers 200 and adds no record.
7. A missing, malformed or wrong signature, or a `t` more than 10 minutes away from the receiver's clock, answers 401, stores nothing, and the response body contains neither the signature nor the body.
8. A body over 64 KiB answers 413. Invalid JSON, a wrong `type` or `apiVersion`, an unknown `severity` or an unexpected shape answers 422. Each stores nothing.
9. A stored record contains only the Data fields: no `filters`, `projectId`, headers or raw body. Over-long title or body text is truncated to its limit. A `permalink` outside the configured Langfuse base URL is dropped.
10. With more than 500 records, the file keeps the newest 500.
11. The receiver's log lines for accepted and refused requests contain no body text, header value or signature.
12. The receiver starts only when its signing secret is set, and exits non-zero with a message naming the missing key, not its value, otherwise.

**UI**

13. Choosing an identity, creating a thread, sending a message and reading the answer works end to end against the real API subprocess (fake LLM, MCP harness). The answer sections appear in §5 order.
14. A token never appears in the rendered page, the Streamlit log or the page's query string. A test renders every page and searches for the token text.
15. Answer, question, source and alert text containing markdown links or HTML renders as literal text, not a link or element.
16. The approver identity sees a pending approval created by the developer identity, approves it, and the page shows the resulting ticket ID. Rejecting shows "nothing was created". The developer identity gets the 403 envelope when it tries to decide.
17. An API error shows the envelope's code, message and correlation ID, and never a submitted value.
18. The Alerts page lists stored alerts with their severity, refreshes on demand, and says "No alerts received yet" when there are none.
19. The launcher refuses a non-loopback `ui.host` and starts Streamlit with `server.address=127.0.0.1` and usage statistics off.

**Integration with Langfuse (live, local)**

20. With the compose stack up, a signed synthetic alert sent from inside the `langfuse-worker` container to `http://alert-receiver/langfuse/alerts` is stored, and the API's `GET /api/v1/alerts` returns it. This proves reachability and the whitelist.
21. One real alert, fired by Langfuse in a controlled test (for example P8's `devguard-incident-event`), reaches the receiver and is shown on the UI's Alerts page. Evidence: the alert ID, time and a screenshot taken by the developer, recorded in this feature's demo report. If Langfuse's UI refuses the `http://alert-receiver` URL, this criterion is reported NOT MET with the reason, and work stops for a decision (Flagged concerns).

**Docs**

22. `docs/tech.md`, `docs/structure.md`, `docs/threat-model.md` (new threat T18, a forged or hostile alert webhook), `CLAUDE.md` `## Commands` and `docs/index.md` are updated in the same change as the code they describe. A demo runbook in this feature folder lists the start order and the controlled alert test.

## Flagged concerns

- **Langfuse may require an HTTPS webhook URL.** The webhook docs say "HTTPS endpoint"; the hardening docs only restrict ports to 80 and 443. If the UI refuses `http://alert-receiver/...`, implementation stops and asks. TLS on the receiver (with a self-signed CA trusted by Langfuse) is not added silently. *Decides:* Kharisma Wardhana.
- **The Langfuse SSRF whitelist is loosened by exactly one hostname.** `LANGFUSE_WEBHOOK_WHITELISTED_HOST=alert-receiver` lets a Langfuse admin point webhooks at that one compose service, and nothing else. Only the developer holds the Langfuse admin login. Recorded in the threat model (T18). *Decides:* Kharisma Wardhana.
- **New inbound surface (T18).** The receiver accepts POSTs from the compose network. Its controls are the signature, the freshness window, size and schema limits, deduplication, field allowlisting and storing nothing raw. A received alert can never cause an action, and its text is redacted again and rendered as plain text. The receiver is reachable only inside the Podman network, not from the host or the LAN. *Decides:* Kharisma Wardhana.
- **Departure from "PostgreSQL for all durable state".** Alerts go to a capped JSONL file (see Architecture). If the release review wants them in PostgreSQL, this is the place to say so. *Decides:* Kharisma Wardhana.
- **The UI process holds the token-signing secret** in order to mint demo tokens, so anyone who can run the UI can act as any configured demo identity. That's acceptable for a single-developer loopback demo with synthetic identities, and it's the same trust level as `scripts/mint_dev_token.py`. It is stated in the runbook as demo-only. *Decides:* Kharisma Wardhana.
- **Streamlit telemetry.** Streamlit sends usage statistics by default. The launcher turns them off (AC #19), keeping egress within the allowlist (T9).
- **Rendering untrusted text.** Model output and retrieved excerpts may carry planted links or markup (P7 answer echo). Everything is rendered as plain text (AC #15).
- **UX and accessibility:** this is a demo UI for one presenter, not a product UI. It uses Streamlit's default components (keyboard-reachable, with labelled inputs). Severity is shown as text as well as colour. No further accessibility work is planned. *Decides:* Kharisma Wardhana.
- **Deadline:** done by 2026-11-20, together with the rest of P9 and after P8 (user decision). If time runs short, the history route (AC #2) and the auto-refresh are cut first, and that is recorded as a decision, not a silent drop. *Decides:* Kharisma Wardhana.
- **Compliance and brand:** None found. Checked: only synthetic data, no ASPI/BI marks or names in the UI beyond the project title, no personal data, no external service.

## Out of scope

- Memory preferences, memory patterns and feedback in the UI (user decision).
- One-shot `/api/v1/chat` in the UI (threads cover it).
- Streaming responses, websockets and push. Polling is enough for a demo.
- Real login, users, passwords or SSO. Identities are fixed synthetic subjects.
- Any deployment beyond the developer's machine. No non-loopback bind, no TLS, no reverse proxy.
- Acting on an alert: no auto-remediation, no ticket, no agent run.
- Slack, PagerDuty, email or any other alert channel.
- Configuring the Langfuse alerts themselves. That is P8 step 10. P9 only adds the webhook channel they deliver to.
- The release-gate run and final report of PRD P9 (separate work).
- The admin ingestion UI (FR-20).

## Resolved questions

- **Deadline:** 2026-11-20, the same as the overall deadline (user decision).
- **How the UI gets a token:** it mints one in-process from the existing signing secret for a fixed list of synthetic demo identities, with the API audience and a 15-minute TTL (Behaviour §1; concern recorded).
- **Thread listing:** a new owner-scoped `GET /api/v1/threads`, plus `GET /api/v1/threads/{id}/messages` for history (Behaviour §2).
- **Langfuse webhook format:**
  - `type: "monitor-alert"`, `apiVersion: "v1"`;
  - `payload` with `monitorId`, `projectId`, `permalink`, `message.title/body`, `severity`, `timestamp`, `fromTimestamp`, `toTimestamp`, `view`, `filters`, `window`;
  - signed in `x-langfuse-signature: t=<ts>,v1=<hex HMAC-SHA256 of "<ts>.<raw body>">`;
  - delivered only to ports 80 and 443;
  - private and loopback targets refused unless whitelisted.

  Source: the Langfuse alerts, webhooks and self-host hardening docs, read 2026-11-12. Whether the UI requires HTTPS is unverified (Flagged concerns).
- **Can Langfuse reach a loopback receiver?** No (measured; see Architecture). Resolved by a receiver container in the compose network (user decision A).
- **Where the receiver runs:** its own compose service, not a route in the API. The API, on the host's loopback port 8000, can't be reached by Langfuse, and 8000 isn't a deliverable port.
- **How alerts reach the UI:** polling `GET /api/v1/alerts` on refresh and optionally every 15 seconds. No push (user agreed).
