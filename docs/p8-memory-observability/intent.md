# Intent: The agent saves only policy-approved memory with tested save/update/delete, every request is a redacted Langfuse trace with §21.2 scores, and an alert is shown firing
*Author: Kharisma Wardhana. Stage: Plan. Written: 2026-11-11. Via /aspec:make-it-simple-v2.*

## Problem statement

The PRD's P8 exit criterion, *save/update/delete tests and alert firing evidence*, isn't met. Today:

- **There is no long-term memory.** The `memory_decision` node always returns `not_saved` (`app/agent/graph.py`). There is no memory table, no save/update/delete/expiry rule, and no isolation between users (TRD §16, threat T11).
- **There is no observability.** Langfuse 4.76.0 has run locally since P1, but no application code sends it anything. A diagnosis can't be followed across its spans: sanitization, classification, retrieval, reranking, MCP calls, the LLM call, the approval wait and the policy check (TRD §21.1). There are no §21.2 scores and no alert (§21.3). Threat T6 still lists "Langfuse redaction remains P8".
- **Items handed over by earlier phases are still open:**
  - **Retention (P6).** Checkpoints (30 days) and approvals and tickets (90 days) accumulate, because nothing deletes them (TRD §19.3).
  - **Single-event alerts (P6/P7).** Any `APPROVAL_*`, `AUDIT_UNAVAILABLE` or `INTERNAL_ERROR` should raise an alert by itself. P7's monitoring passed these to P8 rather than to a control band.
  - **Answer echo (P7).** On the live model, the answer repeated injected or planted attack text in 5 of 10 cases. Authority never expanded, but the answer can quote hostile text to the reader. Nothing scores this, and nothing prevents it.

## Proposed outcome

- **Long-term memory with its own policy and table,** separate from checkpoints (TRD §16, user decision on scope):
  - **User preference.** Saved only when the user states it explicitly, such as a preferred language or output format. It has its own review/expiry date.
  - **Verified troubleshooting pattern.** Saved only after a reviewer approves it, and stored sanitized.
  - **Project context is not saved in P8** (user decision).
  - **Never saved:** credentials, personal or merchant data, and unverified agent inferences. Default behaviour is not to save, and `memory_decision` records the reason for every save or no-save.
  - **Update** only on explicit, newer information, keeping version history.
  - **Delete** on user or admin request. **Expiry** after 90 days by default.
  - **Isolation:** memory is scoped to its owner, and one user's memory never reaches another user.
  - Save, update, delete, expiry and isolation each have a test.
- **One Langfuse trace per request** with child spans for every §21.1 step. Each records `thread_id`, `run_id`, model and revision, prompt version, corpus version, tool name, latency, token usage and sanitized status. It joins to P7's audit rows through the run and correlation IDs.
  - Redaction runs **before** export.
  - A test sends a synthetic secret through a full run and finds it in no exported trace.
- **§21.2 scores on every trace:** `citation_valid`, `answer_supported`, `tool_success`, `policy_pass` and `user_feedback`, plus a new **answer-echo** score.
- **Answer-echo fencing.** Instruction-shaped text in retrieved chunks is fenced or stripped before it can appear in the composed answer. The P7 injection cases are re-run to measure the echo rate before and after.
- **Alerts:**
  - The mandatory §21.3 alert (p95 latency, or the `policy_pass` rate), with evidence that it fired: the condition, window and threshold as recorded, plus a screenshot.
  - Single-event alerts on `APPROVAL_*`, `AUDIT_UNAVAILABLE` and `INTERNAL_ERROR`.
- **Retention jobs.** Checkpoints are deleted after 30 days, approvals and tickets after 90, and memory rows after their expiry date. Each job has a test showing it deletes only what is past retention.
- **Evidence report** in `docs/p8-memory-observability/`. Measured results only.

## Affected users and systems

- Kharisma Wardhana: runs the local `-m db` suite and the one live pod session, captures the alert evidence, and approves verified patterns as the reviewer.
- `app/agent/`: the `memory_decision` policy, span instrumentation, scores, and answer-echo fencing in compose.
- `app/memory/` (planned in TRD §24): memory policy and storage.
- `app/observability/` (planned in TRD §24): Langfuse client, redaction-before-export, scores.
- `app/api/`: memory routes (list, delete; save of a preference), user feedback for the `user_feedback` score.
- `app/db/`: migration for the memory table, a grant for the app role, and retention jobs.
- `app/mcp_server/`: spans or correlation for tool calls, if the trace needs them from the server side.
- `configs/`: memory expiry, retention periods, Langfuse settings and alert thresholds. `security.yaml` changes only if needed, with the threat model reviewed alongside.
- `docker-compose.yml` / Langfuse: the local instance receives traces. It stays on loopback with telemetry off.
- `docs/threat-model.md`: T6 (Langfuse redaction), T3 (echo), T11 (memory isolation) gain P8 evidence.
- Downstream: P9 (release gates `langfuse_alert_fired` and the final report).

## Constraints

- **Deadline:** P8 is done by 2026-11-15, leaving time for P9 before the 2026-11-20 overall deadline (user decision).
- **GPU:** one short pod session for live trace and alert evidence. The pod is terminated afterwards (user decision).
- **Langfuse stays self-hosted on loopback** with telemetry off. No trace goes to Langfuse Cloud or anywhere else (threat model B5, egress allowlist).
- **No secrets anywhere:** redaction before database persistence, checkpoint serialization and Langfuse export (TRD §19.2). Only synthetic data.
- **Memory defaults to not saving.** Memory never holds credentials, personal or merchant data, or unverified inferences (TRD §16; automatic-FAIL invariant).
- **Tracing must not break a request.** If Langfuse is down, the diagnosis still completes.
- **Must not break:** P2–P7 behaviour and all existing tests, including P6's crash/resume and exactly-once guarantees and P7's §22.5 security suite.
- **Targets aren't results:** the report states measured outcomes only, and alert thresholds are recorded as set, not as calibrated.

## Open questions

- Does self-hosted Langfuse 4.76.0 (OSS) offer alerts at all, or are alerts a Cloud or Enterprise feature? If they are unavailable, what counts as "alert firing evidence": an alert evaluated by our own code over Langfuse's API or the audit table, with a recorded notification?
- Which mandatory alert: p95 latency with a controlled delay in a test node, or the `policy_pass` rate? What window and threshold for the controlled test?
- How are spans produced: the Langfuse SDK's LangChain/LangGraph callback, explicit spans per node, or both? How is redaction guaranteed for everything the callback would capture automatically (prompts, tool inputs, state)?
- How are `answer_supported` and `answer_echo` scored without an LLM judge deciding validity? A deterministic check against the cited chunks and the planted phrases, or something else?
- Where does `user_feedback` come from: a new `/api/v1` route, and what values does it accept?
- How does a user state a preference explicitly: a chat phrase the classifier recognises, an API route, or both? How are saved preferences applied to later answers?
- Verified patterns: does approval reuse P6's approval table and interrupt, or does it go through a separate reviewer route? Which role reviews? Are saved patterns ever shown to the agent, and if so, as untrusted context that is never cited as a source?
- Which database role owns memory reads and writes, and which role runs the retention jobs (owner, like `purge_audit.py`)?
- Answer-echo fencing: strip instruction-shaped text from chunks before compose, fence it in the prompt, or post-check the answer? How does fencing avoid hiding legitimate spec text?
- Do the single-event alerts read from Langfuse (error spans) or from the P7 `tool_audit_logs` table?
