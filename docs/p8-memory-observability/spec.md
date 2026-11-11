# Spec: The agent saves only policy-approved memory with tested save/update/delete, every request is a redacted Langfuse trace with §21.2 scores, and an alert is shown firing
*Author: Kharisma Wardhana. Stage: Design. Written: 2026-11-11. Via /aspec:make-it-simple-v2.*
*Implements: docs/p8-memory-observability/intent.md*

## Behaviour

### 1. Long-term memory (TRD §16)

Memory is separate from checkpoint state. It lives in its own table and follows its own policy. Two kinds are saved (user decision). **Default behaviour is to save nothing.**

**User preferences.** These belong to one user and are never visible to another.

- **Allowed keys and values** are a fixed list in `configs/`. P8 ships two:
  - `language`: `id` or `en`
  - `detail`: `brief` or `detailed`

  Any other key or value is refused with `VALIDATION_ERROR`. Free text is never stored.
- **Ways to save** (user decision):
  - **API route:** `PUT /api/v1/memory/preferences/{key}` with `{"value": ...}`. Also `GET /api/v1/memory/preferences` and `DELETE /api/v1/memory/preferences/{key}`.
  - **Chat phrase:** a fixed phrase in a chat message, matched by a configured pattern table in `memory_decision`, for example `remember: answer in Indonesian` or `ingat: jawab singkat`. Each pattern maps to exactly one allowed key and value. The model never decides what is saved; only the pattern table does.
- **Update.** Saving an existing key with a new value updates it. The version increases by one and the previous value is copied to the history table. Saving the same value again changes nothing.
- **Expiry.** A preference has its own review date: `preference_ttl_days` in `configs/` (proposed 180). After that it is no longer applied, and the retention job removes it.
- **Use.** At the start of a run, the caller's active preferences are loaded. They become one line in the prompt's trusted `<notes>` block, for example "Answer in Indonesian" or "Keep the answer brief". A preference never changes routing, tools, tool arguments, risk or policy.

**Verified troubleshooting patterns.** These are shared with the team after review (user decision).

- **Propose.** `POST /api/v1/memory/patterns` takes `thread_id`, `title` (up to 120 characters), `text` (up to 1,000 characters), and optionally `api_family` and `intent` tags.
  - The caller must own the thread. Any role that can chat may propose.
  - The pattern is stored as `pending`, with the thread's last run as its source.
  - **Secret-shaped or personal-data-shaped text is refused, not redacted:** credentials per `redact_fields`, PEM blocks, bearer tokens, email addresses, phone numbers, and long digit runs that look like account or transaction IDs. A redacted pattern would be misleading, so the proposer must rewrite it.
- **Review.** `POST /api/v1/memory/patterns/{id}/decision` with `approve` or `reject` and an optional reason.
  - Only `approver_roles` (`support_engineer`, `admin`) may decide.
  - The reviewer may not be the proposer. The database enforces this too, like the P6 approval rule that requester and approver differ.
  - Approve makes the pattern `active` with `approved_by` set. Reject makes it `rejected`.
  - A decision on a pattern that is not `pending` gets `409 CONFLICT`.
- **Use.** At retrieval time, up to `max_patterns_in_prompt` (proposed 2) active, unexpired patterns whose `api_family` or `intent` tags match the run are added to the prompt. They go in a separate `<verified_patterns untrusted="true">` block, neutralised like evidence.
  - Patterns are **never** sources. They have no citation ID, and the existing policy check still removes any citation that does not resolve to a chunk retrieved in this run.
  - The answer's limitations note when a pattern was shown.
- **Expiry.** 90 days (`pattern_ttl_days`).
- **Delete.**
  - The proposer may delete a pattern in any status. An admin may delete any pattern.
  - Deleting soft-deletes the row and blanks its text and title in the row and in its history. Only the metadata remains (who, when, status).
  - The retention job hard-deletes it later.
- **Listing.** `GET /api/v1/memory/patterns` returns active patterns, plus the caller's own pending ones. Approvers also see all pending patterns.

**`memory_decision` node.** It runs on every completed run, as today. It now records one reason code in state, in the API response and in the trace:

- `saved_preference` / `updated_preference` / `unchanged_preference`: a configured phrase matched.
- `not_saved:default`: no explicit save request (the normal case).
- `not_saved:secret_detected`: the run's input contained a secret. Nothing from that run is saved, even if a phrase matched.
- `not_saved:unrecognised_preference`: a "remember"-like prefix with no matching pattern. A limitation line lists what can be saved.
- `not_saved:route`: refusals, clarifications and draft-only runs never save.

Patterns are never saved by the node. They are proposed only through the route.

**Isolation (T11).** Every preference query is scoped to `owner_id = sub`. Pattern reads return only active patterns, or pending ones the caller may see. Another user's preference is never loaded into a run and never listed.

### 2. Langfuse tracing (TRD §21.1)

- **One trace per run.** The trace ID is derived deterministically from `run_id`, using the Langfuse SDK's seeded trace ID. The agent and the MCP server therefore attach spans to the same trace without passing anything new over the wire. P7 already sends the run ID with every MCP call.
  - A resumed run, after an approval decision, gets its own trace. The `thread_id` (session ID) links it to the first.
- **Trace attributes:**
  - `session_id` = `thread_id`; `user_id` = `sub` (synthetic test subjects only).
  - `environment` = `capstone`; tags `service:agent` and `prompt:<version>`.
  - metadata: `run_id`, the correlation ID, model name and revision (from `configs/model.yaml`), prompt version, corpus version (the source-manifest checksum the search tool reports), intent, risk, route and the final status.
- **Child spans:**
  - Agent side, one span per graph node, with latency: `sanitize_input`, `classify_intent`, `classify_risk`, evidence nodes, `select_tools`, `compose_diagnosis`, `policy_check`, `memory_decision`, `finalize`, and on a P6 run `draft_action` and `execute_approved_action`.
  - The LLM call is a **generation** observation with model, token usage, latency, prompt version, attempts and the fallback flag.
  - Every MCP call is a span on the agent side, with tool name, outcome code and latency. It never carries arguments.
  - Server side, the MCP server adds spans for `search_qris_spec`: dense retrieval, BM25 retrieval and fusion, and reranking when enabled. Validator tools get one span each with the result status.
  - The approval wait is a span from the approval's `created_at` to its `decided_at`. It is recorded on the resume run's trace, because the wait itself spans two processes and possibly a restart.
- **What is exported: an allowlist.** Spans carry IDs, names, codes, counts, statuses, latencies and token counts only.
  - The user message, the answer text, chunk text, tool arguments, payloads and pattern text are **never** set as span input or output.
  - A **redaction hook** (`mask_otel_spans`) runs at export as a second layer. It applies the `redact_fields` and value-pattern rules to every string attribute. If it raises, the SDK drops the batch rather than exporting it unredacted.
- **Tracing never breaks a request.** The SDK exports in the background, so a request finishes even when Langfuse is down or slow.
  - Tracing is off when the keys are missing or `observability.enabled` is false. That is the CI default.
  - Tests use an in-memory exporter.
- **Langfuse stays local.** The host comes from `LANGFUSE_HOST`. The process refuses to start tracing if that host is not in `egress_allowlist` (`localhost`; the existing egress check).

### 3. Scores (TRD §21.2, plus answer echo)

Scores are computed **deterministically** after `finalize` and attached to the run's trace. No LLM judges anything.

| Score | Type | Value |
|---|---|---|
| `citation_valid` | boolean | 1 if every citation in the final answer resolves to a chunk retrieved in this run, else 0. Not sent for refusals or clarifications. |
| `answer_supported` | boolean | 1 if every finding and next step carries at least one resolving citation or validator reference, and every `confirmed` finding has a decisive validator result or a citation. A structural check, not semantic entailment (see Flagged concerns). |
| `tool_success` | boolean | 1 if every MCP call in the run returned OK. Not sent when no tool was called. |
| `policy_pass` | boolean | 1 if `policy_check` had to correct nothing (no removed or downgraded claim). Refusals and clarifications score 1. |
| `answer_echo` | boolean | 1 if the composed answer, **before** fencing, contained instruction-shaped text (section 4). |
| `user_feedback` | categorical | `up` or `down`, from `POST /api/v1/threads/{thread_id}/feedback`. Owner only; applies to the thread's last run. |
| `incident_event` | boolean | 1, with the code in the comment, whenever a single-event signal occurs (section 5). Sent only when it happens. |

### 4. Answer-echo fencing (P7 finding)

The prompt already marks evidence as untrusted (`compose-v1`), yet the live model repeated attack text in 5 of 10 cases. So fencing is a **deterministic step in code**, in two places. It works from one marker list, `injection_markers` in `configs/security.yaml`. The list holds generic categories, not the P7 canaries:

- override and role phrases ("ignore previous/above instructions", "system override", "you are now", "new instructions")
- approval claims ("pre-approved", "approval granted", "already authorised")
- tool or command invocation ("call/run/execute <snake_case_identifier>", SQL verbs followed by a table)
- secret variable names (`*_API_KEY`, `*_SECRET`, `*_TOKEN`, `*_PASSWORD`)
- metadata-service and internal-address shapes

The two places:

1. **Before compose.** In each evidence excerpt, a sentence matching a marker is replaced with `[instruction-like text removed]` in the prompt only. The `sources` list keeps the original quote, so citations still resolve to the stored chunk.
2. **After compose.** In every generated text field (`summary`, `findings[].text`, `next_steps[].text`, `limitations`), a sentence matching a marker is removed. A finding or next step left empty is dropped. One limitation line says text resembling an instruction from untrusted input was removed.

`answer_echo` is measured on the text before step 2, so the same run shows both whether the model echoed and whether the user saw it. The prompt version becomes `compose-v2`, because the evidence block changes.

### 5. Alerts (TRD §21.3)

Configured in the local Langfuse UI. Langfuse 4.76.0 is v4, and v4 offers alerts in OSS self-hosting without a licence key.

- **Mandatory alert:** `devguard-agent-p95-latency-high`.
  - Source: Observations. Metric: p95 latency. Filter: environment `capstone`, tag `service:agent`.
  - Window and threshold: the shortest window Langfuse offers, with a threshold below the measured baseline, both recorded as set.
  - **Controlled test.** A test-only delay, `DEVGUARD_TEST_DELAY_MS` (0 by default, capped at 5,000 ms), is applied inside the `classify_intent` span when set. Enough runs are sent to breach the window. The alert state is captured as evidence.
- **Single-event alert:** `devguard-incident-event`.
  - Source: Scores (boolean) `incident_event`. Metric: count at least 1 in the window.
  - Triggers: any MCP call outcome `APPROVAL_*`, `AUDIT_UNAVAILABLE` or `INTERNAL_ERROR` seen by the agent, and any API `INTERNAL_ERROR` (the crash handler sends a one-span `api-error` trace with the code only).
  - This replaces the "alert, not band" items from P6 and P7 monitoring.
- **Evidence:** the condition, window, threshold and channel as configured, the alert's state and history after the controlled test (screenshots taken by the developer), and the run IDs used. Measured results only.

### 6. Retention jobs (TRD §19.3, P6 hand-off)

A developer command run as the owner role, like `purge_audit.py`. Nothing schedules it. Periods come from `configs/`:

- **Checkpoints, after 30 days:** all LangGraph checkpoint rows for a thread whose `updated_at` is older than 30 days and whose status is not `awaiting_approval`.
- **Tickets, approvals and threads, after 90 days:** sandbox tickets, then their approvals, then threads with no remaining approvals and no newer activity. This order respects the foreign keys.
- **Memory:** rows soft-deleted or past `expires_at` are hard-deleted, with their history.
- **`--dry-run`** prints the counts per table without deleting.
- The output prints counts only, never the DSN or row content.
- The existing `purge_audit.py` is unchanged.

## Architecture and the reason for it

- **Memory in PostgreSQL**, in two new tables: memory rows and append-only history. The database is already there, migrations and least-privilege roles exist (P7), and TRD §16.3 suggests the schema.
  - The `devguard_app` role gets `SELECT, INSERT, UPDATE` on the memory table and `SELECT, INSERT` on history. It gets **no `DELETE`**: user deletes are soft deletes, and hard deletes run as the owner in the retention job. This is the same split as `purge_audit.py`.
- **Memory code in its own package**, as planned in TRD §24 (`app/memory`), so the policy is testable without the graph: phrase table, allowed values, sanitization, the decision reasons, and storage.
- **Preferences enter the prompt only as a trusted note**, and patterns only as an untrusted block. Neither touches routing or tools, so the P5/P7 invariant "no model chooses a node, a tool or a tool argument" still holds.
- **Tracing through the Langfuse Python SDK** (OpenTelemetry based) with **explicit spans**, not the LangChain callback handler. The callback captures node inputs and outputs automatically, which would export prompts, chunk text and state. Explicit spans with an allowlist make "no secrets in traces" true by construction, and `mask_otel_spans` is the second layer.
  - Node spans hang off the existing `_timed` node wrapper in the graph, so no node changes for tracing.
- **Shared trace ID from the run ID**, so the MCP server's retrieval spans join the agent's trace without a new header or argument. P7's run-ID plumbing is reused, and the MCP argument models stay unchanged (strict Pydantic, no new fields).
- **The observability code in its own package** (`app/observability`, TRD §24): client setup, redaction hook, span helpers and score computation. A no-op implementation is used when tracing is off.
- **Fencing in code, not the prompt.** The prompt approach is already in place and measured insufficient. A deterministic filter is testable and keeps "the LLM only explains".
- **Alerts in Langfuse's own alert feature**, not in our code. That is what §21.3 names, and it is available on our version.
- **New dependency:** the `langfuse` Python SDK (with OpenTelemetry). The SDK major version must be the one that supports a Langfuse v4 server. It is pinned in `uv.lock` and covered by the existing weekly `pip-audit`.
- **Conformance:** this conforms to `docs/tech.md` and `docs/structure.md`. Langfuse is already in the stack, and `app/memory` and `app/observability` are TRD §24 packages. **It departs from P5 in one place:** P5 settled prompt `compose-v1` and the answer schema. P8 changes the prompt to `compose-v2` (fenced evidence, an optional patterns block, a preference note). The answer schema is unchanged. The P5/P6 scripted flows must still pass (flagged below).

## Data

| Store | What | Sensitivity | Retention |
|---|---|---|---|
| Memory table | owner (sub, or the team scope for patterns), type (`user_preference` / `verified_pattern`), key, sanitized content (an allowed value, or pattern text up to 1,000 characters), tags, status (`active`/`pending`/`rejected`), proposer, approver, source thread and run, confidence (`verified` for patterns), created/updated/expires/deleted timestamps, version | Synthetic, sanitized. Patterns refuse secret-shaped and personal-data-shaped text. Preferences are enum values only. | Preferences 180 days (review date), patterns 90 days; hard-deleted by the retention job after expiry or soft delete |
| Memory history | memory ID, version, previous content, changed by, changed at | As above; blanked when the memory is deleted | Deleted with its memory row |
| Langfuse (ClickHouse, local) | Traces, spans, generations and scores: IDs, names, codes, counts, latencies, token counts, `sub` | No message, answer, chunk, argument or memory text (allowlist + mask hook) | Kept until the local volume is removed: Langfuse retention policies need an Enterprise licence (flagged) |
| Checkpoints / approvals / tickets / threads (P6) | Unchanged | Unchanged | Now enforced: 30 / 90 / 90 / 90 days by the retention job |

## Acceptance criteria

**Memory**

1. `PUT /api/v1/memory/preferences/language` with `{"value": "id"}` saves version 1. A second `PUT` with `en` makes version 2 and writes one history row holding `id`. A third `PUT` with `en` leaves version 2.
2. A key or value outside the configured list gets `422 VALIDATION_ERROR` in the §18 envelope, and no row is written.
3. A chat message containing a configured phrase (`remember: answer in Indonesian`) saves `language=id`, and the response's `memory_decision` is `saved_preference`. A message with no phrase gives `not_saved:default`. A "remember: …" with no matching pattern gives `not_saved:unrecognised_preference`. A message that also carries a secret gives `not_saved:secret_detected` and saves nothing.
4. `DELETE /api/v1/memory/preferences/language` stops the preference being applied to the next run, and the row reads as deleted. After the retention job, neither the row nor its history exists.
5. A preference past its expiry date is not applied and not listed. The retention job removes it, and keeps a row one day inside expiry.
6. **Isolation:** user A's preference is never listed for user B and never applied to B's run, checked through both the API and the prompt the fake LLM receives. B cannot delete A's preference: `DELETE` on a key B never saved is `404`.
7. A pattern proposed for a thread the caller does not own gets `404`. A pattern whose text contains a secret-shaped or personal-data-shaped value gets `422`, and nothing is stored.
8. A `developer` or `kb_maintainer` deciding a pattern gets `403`. The proposer deciding their own pattern gets `403`, and the database rejects the same update made directly. A decision on a non-pending pattern gets `409`.
9. An approved pattern whose tags match a run appears in that run's prompt inside `<verified_patterns untrusted="true">`, and is absent from `sources`. A citation the model invents for it is removed by `policy_check`. A rejected, pending, deleted or expired pattern never appears.
10. Deleting a pattern blanks its title and text in the row and in all history rows. The proposer and admins can delete; others get `404` or `403`.
11. A preference never changes the tool calls of a run: the same request with and without `language=id` makes identical MCP calls.

**Tracing and scores**

12. With the in-memory exporter, a P5 read flow produces one trace with the §21.1 spans: one per executed node, a generation for the LLM call, and one span per MCP call. Each carries `run_id`, `thread_id` as session, the model name, prompt version, corpus version, latency and token usage.
13. The MCP server's `search_qris_spec` spans (dense, BM25 + fusion, rerank when enabled) carry the same trace ID as the agent's spans for that run.
14. **No secret in any trace:** a synthetic `client_secret`, bearer token, PEM block and signature sent through a full escalation and approval appear in no exported span attribute, event or score comment. The check reads the whole exporter buffer, not one span. The user message and answer text do not appear in any span either.
15. If the mask hook raises, nothing from that batch is exported (fail closed).
16. With `LANGFUSE_HOST` pointing at a refused or blackholed loopback port, the chat request still returns `200` with an answer, within the existing request timeout.
17. A `LANGFUSE_HOST` outside `egress_allowlist` disables tracing with one log line and never sends anything.
18. Each run gets `citation_valid`, `answer_supported`, `policy_pass`, `answer_echo`, and `tool_success` when a tool was called, with values matching fixtures:
    - an answer with an unresolvable citation → `citation_valid=0`;
    - a finding with no reference → `answer_supported=0`;
    - a policy correction → `policy_pass=0`;
    - a failed tool → `tool_success=0`.
19. `POST /api/v1/threads/{id}/feedback` with `up` or `down` from the owner records `user_feedback` on the thread's last run. A non-owner gets `404`, and any other value gets `422`.
20. An MCP outcome `APPROVAL_INVALID` (or `AUDIT_UNAVAILABLE`, `INTERNAL_ERROR`) in a run produces exactly one `incident_event=1` score with the code as its comment. A forced API crash produces an `api-error` trace with `incident_event=1` and no stack trace or message text.

**Fencing**

21. An evidence excerpt containing a marker sentence reaches the prompt with that sentence replaced, and the run's `sources` still show the original quote.
22. A composed answer containing a marker sentence is returned without it, plus one limitation line. `answer_echo=1` is recorded. With no marker, nothing changes and `answer_echo=0`.
23. In CI mode, the P7 injection suite still passes all 10 gate checks. On the live model, the echo rate the user sees (`answer_echoes_attack_phrase`) is measured on the same 10 cases and reported next to P7's 5/10, together with the pre-fencing `answer_echo` rate. Measured, not targeted.
24. **False-positive check:** the marker list run over every retrievable chunk of the approved corpus reports its match count. The P5/P6 scripted flows (`eval_agent.py`, CI mode) still pass with no fencing applied.

**Alerts and retention**

25. `devguard-agent-p95-latency-high` and `devguard-incident-event` exist in the local Langfuse with their condition, window and threshold recorded. After the controlled test, each shows the ALERT state at least once. The evidence report links the screenshots and the run IDs.
26. The retention command with `--dry-run` prints per-table counts and deletes nothing. Without `--dry-run`, against seeded rows just outside and just inside each period, it deletes exactly the rows outside. It keeps the checkpoints of an `awaiting_approval` thread, and it violates no foreign key.
27. The `devguard_app` role cannot `DELETE` from the memory tables. That role cannot run the retention command's deletes; the owner can.

**Regression**

28. `uv run pytest` passes, including P6 crash/resume, exactly-once, the P7 §22.5 suite and the repo-hygiene checks. `uv run pytest -m db` passes locally.

## Flagged concerns

- **`answer_supported` is structural, not semantic.** It proves every claim points at something retrieved or validated. It does not prove the cited text says what the claim says. An LLM judge would break "the LLM never decides validity" and add cost. The report must name the score for what it is. *Decides:* Kharisma Wardhana.
- **The fencing marker list is written by someone who has seen the P7 attack phrases.** The measured echo drop on those 10 cases may overstate how well it generalises. Mitigation: the list holds generic categories, not canaries, and the report says the cases are not held out. A held-out set is P9 work. *Decides:* Kharisma Wardhana.
- **Fencing can remove legitimate text.** A spec sentence such as "the merchant must call the token endpoint first" could look like a command. The false-positive count over the whole corpus (AC 24) is reported. The `sources` quote is never altered, so the reader can still see the original. *Decides:* Kharisma Wardhana.
- **Shared patterns are cross-user memory.** §16.2 allows team scope after review. The controls are four-eyes review, refusal of secret- and personal-data-shaped text, and display as untrusted, uncited notes. Regex refusal can miss personal data, so the reviewer is the real control. *Decides:* Kharisma Wardhana (as the reviewer role owner).
- **A poisoned pattern (memory poisoning, T3 family).** An approved pattern containing an instruction is shown to the model on later runs. It is neutralised, marked untrusted and fenced like evidence, it cannot expand tool authority (no model-chosen tools), and its sentences pass through the same after-compose fence. The threat model gains this as a P8 row. *Decides:* Kharisma Wardhana.
- **Langfuse keeps traces indefinitely.** Its retention policies need an Enterprise licence. Traces hold no content (allowlist), so nothing a user deletes from memory lives on in Langfuse. Removing traces means removing the local volume, which will be documented. *Decides:* Kharisma Wardhana.
- **Alert notification channel.** Langfuse alerts may require a channel (Slack, PagerDuty, webhook, GitHub Actions). The only local option is a webhook into a receiver. A receiver reachable from the Langfuse container may need a bind address other than `127.0.0.1`, which would break the loopback-only rule. If the alert can't be created and shown without that, implementation stops and asks. A non-loopback bind is never chosen silently. *Decides:* Kharisma Wardhana.
- **The prompt changes (`compose-v1` → `compose-v2`)**, departing from the P5 decision. The answer schema and verdict rules stay the same. The P5/P6 flows and P7 injection gate must stay green (AC 23, 28). *Decides:* Kharisma Wardhana.
- **`sub` goes to Langfuse as the user ID.** All subjects are synthetic test identities and Langfuse is local. If real identities ever arrive, this must become a hash. *Decides:* Kharisma Wardhana.
- **Deadline risk.** This is a large P8 for four days (done by 2026-11-15). The plan orders work so that the PRD exit criteria come first: memory save/update/delete, traces and redaction, and the alert. Fencing and retention come after. If time runs out, what is cut is a decision, not a silent drop. *Decides:* Kharisma Wardhana.
- **Privacy (Indonesian data protection):** only synthetic data, and no personal data is stored by policy. Production use would need review (TRD §19.3).
- **UX, accessibility and brand:** None found. P8 adds JSON API routes only, with no UI. Checked: no page, form or rendered output is added. The alert screenshots are evidence, not product UI.

## Out of scope

- Project-context memory (user decision); any memory kind not listed above; editing a pattern in place (propose a new one, delete the old).
- LLM-as-judge scores, and semantic `answer_supported`.
- Langfuse Cloud, Slack, PagerDuty or email channels; Langfuse's own retention policies (Enterprise).
- Scheduling the retention job (a developer command, like `purge_audit.py`); backups (none exist locally).
- Tracing the ingestion and eval scripts; dashboards beyond what the alert evidence needs.
- A held-out injection set (P9); calibrating the operational p95 threshold from real traffic (no real traffic exists).
- Any production endpoint, credential or data.

## Resolved questions

- **Does self-hosted Langfuse 4.76.0 offer alerts?** Yes. Langfuse's self-host feature table lists Alerts as available on Open Source from v4, and the licence-key page does not list alerts among the Enterprise features. Errors are not a native alert source, so single events are recorded as a boolean score (`incident_event`) and alerted on. If a channel is required and can't be received on loopback, implementation stops and asks (Flagged concerns).
- **Which mandatory alert, window and threshold?** p95 latency over observations, filter `capstone` and `service:agent`, made to fire by a capped test-only delay in one node. Window: the shortest Langfuse offers. Threshold: below the measured baseline. Both are recorded as set (Behaviour §5).
- **How are spans produced and redaction guaranteed?** Explicit SDK spans on the existing node wrapper and MCP call path, not the LangChain callback. An attribute allowlist exports no text, and the `mask_otel_spans` hook applies `redact_fields` as a fail-closed second layer (Behaviour §2, AC 14–15).
- **How are `answer_supported` and `answer_echo` scored without an LLM judge?** Deterministically. Structural reference checks for `answer_supported`; the `injection_markers` match on the answer before fencing for `answer_echo` (Behaviour §3–4).
- **Where does `user_feedback` come from?** `POST /api/v1/threads/{thread_id}/feedback`, values `up` or `down`, owner only, applied to the thread's last run (AC 19).
- **How does a user state a preference explicitly, and how is it applied?** Through the preferences API route and fixed chat phrases from a configured pattern table (user decision). It is applied as one trusted note line in the prompt, never to routing or tools (Behaviour §1, AC 11).
- **Verified patterns: approval path, reviewer role, agent use?** Any thread owner proposes. A different `support_engineer` or `admin` approves through a dedicated pattern route, not the P6 ticket approval table, because that table is constrained to `create_support_ticket`. Active patterns are shown to the agent as untrusted, never-cited notes (user decision).
- **Which role owns memory reads/writes, and which runs retention?** `devguard_app` reads and writes memory, with soft delete only. The owner role runs the retention command, as with `purge_audit.py` (Architecture).
- **How does fencing avoid hiding legitimate spec text?** Generic marker categories. Excerpts are altered in the prompt only, never in `sources`. A corpus-wide false-positive count and the unchanged P5/P6 flows check it (AC 21–24).
- **Do the single-event alerts read from Langfuse or from `tool_audit_logs`?** From Langfuse, through the `incident_event` score emitted by the agent and the API crash handler. The audit table stays the durable record, with no extra reader (Behaviour §5).
