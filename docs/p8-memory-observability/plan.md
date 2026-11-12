# Plan: The agent saves only policy-approved memory with tested save/update/delete, every request is a redacted Langfuse trace with §21.2 scores, and an alert is shown firing
*Author: Kharisma Wardhana. Stage: Build. Written: 2026-11-11. Via /aspec:make-it-simple-v2.*
*Implements: docs/p8-memory-observability/spec.md*

## Affected files

New: application code
- `app/memory/__init__.py`: package (TRD §24 layout).
- `app/memory/policy.py`: the allowed preference keys and values, the chat phrase table, the pattern refusal checks (secret- and personal-data-shaped text), and the `memory_decision` reason codes ([spec Behaviour §1](spec.md#1-long-term-memory-trd-16)).
- `app/memory/store.py`: preference upsert with versioning and history, soft delete with blanking, listing scoped to the owner, pattern propose/decide/list/delete, and matching patterns by tag. Parameterised SQL only.
- `app/db/migrations/0003_long_term_memory.sql`: the memory and history tables. Includes the four-eyes CHECK (approver differs from proposer) and CHECKs on type, status, key and length ([spec Data](spec.md#data)).
- `app/observability/__init__.py`: package.
- `app/observability/tracing.py`:
  - client setup from `LANGFUSE_*`, plus the egress check of the host;
  - the no-op tracer when tracing is off;
  - the seeded trace ID from `run_id`;
  - span helpers that accept only allowlisted attribute names;
  - the `mask_otel_spans` redaction hook (fail closed).
- `app/observability/scores.py`: deterministic `citation_valid`, `answer_supported`, `tool_success`, `policy_pass`, `answer_echo` and `incident_event` from a run result ([spec Behaviour §3](spec.md#3-scores-trd-212-plus-answer-echo)).
- `app/agent/fence.py`: the marker matcher, evidence-excerpt fencing (prompt only) and answer fencing ([spec Behaviour §4](spec.md#4-answer-echo-fencing-p7-finding)).
- `app/db/retention.py`: the per-table purge queries in foreign-key order, with dry-run counts ([spec Behaviour §6](spec.md#6-retention-jobs-trd-193-p6-hand-off)).

New: scripts
- `scripts/purge_retention.py`: owner-role retention command with `--dry-run`. Prints counts only.
- `scripts/scan_markers.py`: runs `injection_markers` over every retrievable chunk and prints the match count per document (spec AC 24). It reads the local Qdrant through the existing store code and prints no chunk text, only IDs and counts.

New: tests
- `tests/unit/test_memory_policy.py`: phrase table, allowed values, refusal shapes, reason codes.
- `tests/unit/test_memory_store.py`: `db`; versioning, history, soft delete and blanking, expiry, isolation, four eyes in SQL.
- `tests/unit/test_api_memory.py`: preference, pattern and feedback routes in-process (envelopes, 403/404/409/422).
- `tests/unit/test_observability.py`: in-memory exporter; span tree, allowlist, mask hook (redacts, fails closed), seeded trace ID, no-op when off, egress refusal.
- `tests/unit/test_scores.py`: one fixture per score value.
- `tests/unit/test_agent_fence.py`: marker categories, evidence and answer fencing, empty-finding drop, `sources` unchanged.
- `tests/unit/test_retention.py`: `db`; inside and outside each period, `awaiting_approval` kept, foreign-key order, dry run.
- `tests/integration/test_purge_retention_cli.py`: `db`; the real command as a subprocess.
- `tests/integration/test_scan_markers_cli.py`: the real command as a subprocess against the harness corpus.
- `tests/integration/test_tracing_e2e.py`: agent run through the MCP harness with in-memory exporters in both processes (shared trace ID, server retrieval spans, secret-in-trace walk over the whole buffer), plus Langfuse unreachable → `200`.

Edited
- `pyproject.toml`, `uv.lock`: add `langfuse`, at the SDK major version that supports a v4 server, pinned by the lock.
- `configs/agent.yaml`:
  - a `memory` block: preference keys and values, chat phrase table, `preference_ttl_days: 180`, `pattern_ttl_days: 90`, `max_patterns_in_prompt: 2`, title and text limits;
  - an `observability` block: `enabled`, environment `capstone`, service tag, `test_delay_max_ms: 5000`, export timeout;
  - a `retention` block: checkpoints 30, approvals/tickets/threads 90.
  - Model and prompt identity stay where they are.
- `configs/security.yaml`: `injection_markers` (generic categories) and `pattern_refusal` shapes. The threat model is reviewed alongside, per the file's header rule.
- `app/db/roles.py`: `app` grants on the memory tables (`SELECT, INSERT, UPDATE` on memory; `SELECT, INSERT` on history; no `DELETE`). (As built, step 1:) plus `UPDATE (content_sanitized)` on history, so a user delete can blank the history text as spec Behaviour §1 requires; still no `DELETE`.
- `app/db/migrate.py`: applies `0003`. (As built: no edit needed; `migrate.apply` already picks up every numbered file.)
- `app/agent/graph.py`:
  - `_timed` opens a node span and applies `DEVGUARD_TEST_DELAY_MS` (capped) inside `classify_intent` only;
  - `run`/`resume` open the trace and load preferences and matching patterns;
  - `compose_diagnosis` fences evidence and answer, and records the generation;
  - `memory_decision` applies the policy;
  - the approval-wait span is added on resume;
  - scores are sent after finalize.
- `app/agent/prompts.py`: `compose-v2`. Rule 7 becomes "the language of the question, unless a note sets the language". It adds the optional `<verified_patterns untrusted="true">` block, and preference notes in `<notes>`.
- `app/agent/tools.py`: one span per MCP call (tool name, outcome, ms; no arguments) and `incident_event` on the single-event codes.
- `app/agent/state.py`: `preferences`, `patterns_shown` and `answer_echo` keys.
- `app/agent/answer.py`: the limitation lines for fenced text, shown patterns and unrecognised preferences.
- `app/agent/harness.py`: optional in-memory exporter for the MCP subprocess (test-only environment flag). (As built, step 6:) a `tracer` argument instead; the harness runs in-process.
- `app/agent/config.py` (as built, step 6): `model_identity()` for trace metadata.
- `tests/conftest.py` (as built, step 6): `DEVGUARD_TRACING=0` for every test process and its server subprocesses.
- `app/mcp_server/server.py`: the tool span inside `GuardedTool` (name, outcome, ms), attached to the seeded trace from `current_run_id`.
- `app/mcp_server/tools.py`: retrieval child spans around the embedding, BM25 encoding, the fused Qdrant query and reranking.
- `app/rag/search.py`: `_points` gains an optional timing hook (spans) without changing results. Its existing tests stay unchanged.
- `app/api/app.py`:
  - memory routes (`/api/v1/memory/preferences[/{key}]`, `/api/v1/memory/patterns[/{id}[/decision]]`) and `/api/v1/threads/{id}/feedback`;
  - the crash handler sends the `api-error` trace;
  - the run responses include `memory_decision`, as today.
- `app/api/models.py`: strict request models for the new routes. (As built, step 3:) a 422 uses the API's existing `INVALID_ARGUMENT` code (P5 §18 envelope), not the `VALIDATION_ERROR` named in spec AC 2, so every route keeps one 422 code; a refused pattern lists `refused:<kind>` per field and never the value. Migration `0003` (unapplied outside test schemas) allows `-` in `api_family`, because the API's family values include `qris-mpm`.
- `scripts/api_server.py`, `scripts/mcp_server.py`: initialise and flush tracing at start-up and shutdown.
- `.env.example`: no new key (the `LANGFUSE_*` keys exist). `DEVGUARD_TEST_DELAY_MS` is documented as test-only, not added.
- `tests/unit/test_db_roles.py`, `tests/unit/test_db_migrate.py`, `tests/integration/test_db_migrate_cli.py`: regression for `0003` and the new grants.
- `tests/unit/test_agent_graph.py`, `tests/unit/test_agent_hitl.py`, `tests/integration/test_injection.py`, `tests/integration/test_eval_agent_cli.py`: regression with `compose-v2`. Expected prompt-version strings change; behaviour assertions do not.
- `tests/integration/test_security_suite.py`: secret-in-prompt case extended to the trace exporter buffer.
- `scripts/eval_security.py`: the live injection output also records the pre-fencing `answer_echo` per case.
- `docs/threat-model.md`:
  - T3 gains fencing and the memory-poisoning row;
  - T6 gains Langfuse redaction;
  - T11 gains memory isolation;
  - residual risks gain the spec's flagged items.
- `docs/p8-memory-observability/observability-report.md` (new): measured results (memory tests, trace evidence, alert configuration and screenshots, echo before and after, false-positive count, retention run).
- `docs/p8-memory-observability/alerts.md` (new): how the two alerts were configured in the Langfuse UI, and the controlled test procedure.
- `docs/tech.md`, `docs/structure.md` (`app/memory/` and `app/observability/` exist), `README.md` (retention command, alert test), `docs/index.md`.
- `CLAUDE.md`: see Agent configuration changes.

Not changed: `app/validators/*`, the MCP tool argument models, `app/db/store.py` queries, `app/db/audit.py`, the `0001`/`0002` migrations, `scripts/purge_audit.py`, `docker-compose.yml`, `.github/workflows/*`.

**One correction to the spec's wording, raised for this gate:** spec Behaviour §2 says the corpus version is "the source-manifest checksum the search tool reports". The search tool reports no such field today, and adding one changes the P4 tool contract. The agent will instead compute the same value, the SHA-256 prefix of `knowledge_base/manifests/sources.yaml`, in its own process. The trace attribute and its meaning are unchanged.

## Work sequence

1. **Dependency, configs and migration.**
   - Add `langfuse` (`uv add`), the `memory`/`observability`/`retention` config blocks and `injection_markers`/`pattern_refusal`.
   - Add `0003`, the `roles.py` grants and `migrate.py`.
   - (spec AC 27; building blocks for 1–11)
2. **Memory policy and store.** `app/memory/policy.py`, `app/memory/store.py`. (AC 1, 4, 5, 6, 7, 8, 10)
3. **Memory routes and feedback route.** `app/api/app.py` and `models.py`. Feedback stores nothing locally; it only records the score in step 7. (AC 1, 2, 4, 6, 7, 8, 10, 19)
4. **Memory in the graph.**
   - Load preferences and patterns in `run`, apply the `memory_decision` policy, and add the prompt `compose-v2` notes and patterns block.
   - Update `state.py` and `answer.py`.
   - (AC 3, 9, 11)
   - (As built:) one reason code beyond spec Behaviour §1, `not_saved:store_unavailable`, for a matched phrase when no memory store is configured or the save fails; a memory error never fails a diagnosis. The message response gained `memory_decision` here (it was not returned before P8). `answer_echo` in `state.py` lands with fencing in step 8.
5. **Tracing core.** `app/observability/tracing.py`: client, no-op, seeded ID, allowlist, mask hook, egress check. (AC 14, 15, 16, 17)
   - (As built:) the attribute allowlist is a code constant (`ALLOWED_ATTRS`), so it can't be widened through config. Span helpers catch an escaping exception and record only its class name, because OpenTelemetry would otherwise export the message and stack trace as a span event, which `mask_otel_spans` cannot change. Placeholder keys (`replace-me`) count as missing. The tracer gets its own `TracerProvider`, so the global OpenTelemetry provider is never set. `corpus_version()` and the capped `test_delay_ms()` live here too; step 6 wires them in.
6. **Agent and MCP spans.**
   - Hook `_timed` and add the generation; MCP call spans in `tools.py`.
   - Server tool span and retrieval child spans (`server.py`, `mcp_server/tools.py`, `rag/search.py`).
   - The approval-wait span; tracing set-up in the server scripts.
   - The test-only delay.
   - (AC 12, 13, 14, 16)
   - (As built:)
     - `app/agent/harness.py` takes the server's tracer as an argument, not an environment flag, because the harness runs the MCP app in a thread of the test process.
     - The retrieval spans are `retrieval.bm25_encode`, `retrieval.dense_embed`, `retrieval.fused_query` (the dense and BM25 prefetches with RRF fusion are one Qdrant call) and `retrieval.rerank`.
     - The approval wait is a span on the resume trace whose `ms` attribute is `decided_at − created_at`, because the SDK has no public way to back-date a span's start.
     - On a resume, the MCP server's `create_support_ticket` span lands on the first run's trace. The server knows only `X-DevGuard-Run-Id`, which stays the run's ID because it is the audit join key; the agent's own call span is on the resume trace.
     - There is no correlation ID on the agent's trace: the API creates one only for error envelopes. The server's tool spans carry the MCP correlation ID, which joins them to the audit rows.
     - A LangGraph interrupt is recorded as `outcome=interrupted`, not as an error.
     - The tracer method is `score_run`, matching the feedback route from step 3. `app/agent/config.py` gains `model_identity()`.
     - Both server scripts load `LANGFUSE_*` from `.env`, and `DEVGUARD_TRACING=0` turns tracing off. `tests/conftest.py` sets it, so no test process or server subprocess exports to the local Langfuse.
7. **Scores.** `app/observability/scores.py`; sending scores after finalize; `incident_event` in `tools.py` and the API crash handler; feedback → `user_feedback`. (AC 18, 19, 20)
   - (As built:)
     - `citation_valid` is measured on the composed answer, before `policy_check`. Rule 1 already removes unresolved citations from the final answer, so measured there the score could never be 0.
     - `answer_supported` and `citation_valid` are sent for read runs only, and `answer_supported` is not sent for an abstention, whose next steps are fixed text with nothing to cite.
     - `policy_pass` is 0 for rules 1–5 and 7. Rule 6 (version-conflict note) and rule 8 (abstention) correct no claim.
     - `answer_echo` is not sent until fencing sets it in step 8, so no unmeasured 0 is reported.
     - `incident_event` comes from the run's recorded tool outcomes in `scores.py`, not from `tools.py`; same codes, one score per occurrence.
     - A resume scores only its own calls, on the resume trace.
     - The `api-error` trace is seeded from `api-error:<correlation_id>`, so the response's correlation ID finds it.
8. **Fencing.**
   - `app/agent/fence.py` wired into `compose_diagnosis`, with `answer_echo` measured before the answer fence.
   - `scripts/scan_markers.py`; `eval_security.py` records pre-fence echo.
   - (AC 21, 22, 23 CI part, 24)
   - (As built:)
     - **Sentence splitting.** Sentences split at end punctuation or a blank line, never at a single newline: corpus excerpts are hard-wrapped, and splitting at newlines let a wrapped attack sentence pass unmatched (found by the planted-text test).
     - **Two marker regexes widened** in `configs/security.yaml`, still generic categories rather than P7 canaries: `override` accepts a possessive ("ignore its previous instructions"), and `secret_variable` accepts underscores in the prefix (`MCP_AUTH_SECRET`). The threat model is updated in step 12.
     - **Patterns are fenced too.** Verified patterns get the same prompt-side replacement as evidence.
     - **A summary removed entirely** gets a fixed line.
     - **The run's `trace`** gains `fence` counts (evidence and answer sentences), and the run result gains `answer_echo`.
     - **`scripts/scan_markers.py`** gets a `--harness` mode, so the subprocess test needs no services, plus a hidden test-only `--plant`. It scans whole chunks, so its count is an upper bound for the prompt.
     - **Measured on 2026-11-12** (local Qdrant, retrievable documents): 3 sentences in 2 of 278 chunks. That is 2 `secret_variable` in `snap-technical-security-standard` and 1 `tool_invocation` in `synthetic-qris-troubleshooting-sop`; the SOP one is the false positive "Run `validate_test_signature` with the fixture ID."
     - **`eval_agent.py` in CI mode:** 5/5 flows passed with zero fenced sentences.
9. **Retention.** `app/db/retention.py`, `scripts/purge_retention.py`. (AC 5, 26, 27)
   - (As built:)
     - **Checkpoints are dated by their thread.** LangGraph's checkpoint tables have no timestamp, so a checkpoint's age is its thread's `updated_at`. A checkpoint with no thread row is left alone.
     - **One transaction.** The purge runs as one transaction in foreign-key order. As `devguard_app`, which may delete checkpoints because LangGraph needs to, it fails on the first table it can't delete from and its checkpoint deletes roll back.
     - **The dry run is exact.** `--dry-run` runs the same deletes and rolls back, because an approval only becomes deletable once its ticket is gone, so separate counts would be wrong.
     - **Pending approvals are never purged.** Their threads are kept with them.
10. **Alerts in the local Langfuse.** All on loopback; no pod needed: the p95 alert fires with the delay.
    - **Me:** check that the API sends traces to the local Langfuse, and write `alerts.md`.
    - **You:** create the two alerts in the Langfuse UI (it needs your admin login, which I don't read).
    - **Me:** run the controlled delay test and an `incident_event` test through the API on the fake LLM.
    - **You:** take the screenshots of the ALERT state.
    - **Stop point:** if Langfuse insists on a channel that can only be received off loopback, stop and ask (spec Flagged concerns).
    - (AC 25)
11. **Live pod session.**
    - **You:** start the pod and the tunnel.
    - **Me:** run `eval_security.py --live-injection` (echo before/after fencing) and one live `eval_agent.py --live --api-smoke` run, which gives live traces with real token usage.
    - **You:** terminate the pod.
    - First to cut if time runs short. (AC 23 live part)
12. **Report and docs.**
    - `observability-report.md` from the measured outputs and the `scan_markers.py` count.
    - Threat model; tech, structure, README and index.
    - (AC 25, 28 evidence)
13. **`CLAUDE.md` merge.** Shown as a diff, written only after `approve write`. (Agent configuration)

AC 28 (full suite plus the local `-m db` run, outputs pasted) is checked at every step and again in Phase 4.

The steps run in one session. Steps 2–4 and 5–8 share `app/agent/graph.py` and `app/api/app.py`, so they can't run in parallel worktrees. Step 9 is independent, but it is small.

## Test coverage

Matched rows from `test-scope-by-stage.md`:
- **backend service / script entry point:** subprocess integration tests for the new commands and the real servers;
- **roles/permissions:** auth and authz integration tests on the new routes, and the database role matrix;
- **any database:** regression, because this feature adds tables and grants and changes retention of stored data;
- **existing project:** regression on changed behaviour (prompt version, graph nodes, MCP spans);
- **always:** the agent-config eval.

Not matched: UI/e2e (no screen).

Database tests are `@pytest.mark.db`. They skip in CI with a reason and run locally (P6 decision).

- **Step 1:**
  - **Database (`db`):** `0003` applies once, and a re-run applies nothing.
  - **Role matrix (`db`):** `devguard_app` may select, insert and update memory but gets a permission error on `DELETE`; `devguard_ticket` and `devguard_audit` get permission errors on both memory tables.
  - **Integration:** `db_migrate.py` as a subprocess applies `0003` idempotently.
  - **Regression:** `test_db_migrate*`, `test_db_roles.py` unchanged except for the added rows.
- **Step 2:**
  - **Unit:** phrase matching (English and Indonesian), refusal shapes (secret, PEM, bearer, email, phone, long digit run; strings built from parts), reason codes.
  - **Database (`db`):** version 1 → 2 with one history row; same-value no-op; soft delete blanks row and history; expired rows not listed; A/B isolation; the SQL four-eyes CHECK rejects a self-approval made directly.
- **Step 3 (auth/authz, in-process API with real tokens):**
  - preferences: own save/list/delete, a foreign key → `404`, a bad value → `422`;
  - patterns: a foreign thread → `404`, a secret-shaped text → `422`, developer/kb_maintainer decide → `403`, proposer decide → `403`, non-pending → `409`;
  - feedback: owner → `204`, non-owner → `404`, bad value → `422`.

  **Integration:** the routes against the real API subprocess, with a bad and a missing token (`401`) and a wrong-audience token.
- **Step 4:**
  - **Unit (fake LLM capturing the prompt):** the phrase saves and the reason is recorded; a secret blocks the save; user A's preference never reaches B's prompt; a preference leaves tool calls identical; an active matching pattern appears in the untrusted block and not in `sources`, and an invented citation for it is removed.
  - **Regression:** the P5/P6 graph and HITL tests pass with `compose-v2`.
- **Step 5 (unit):** no-op when keys are missing or disabled; host outside the allowlist → disabled with one log line and no socket opened; allowlist rejects a text attribute; the mask hook redacts each `redact_fields` name and value pattern; the mask hook raising → batch dropped (in-memory exporter receives nothing); seeded ID is stable.
- **Step 6:**
  - **Integration:** a harness run with in-memory exporters in both processes gives one trace ID, a node span per executed node, the generation with usage, MCP call spans and the server retrieval spans.
  - **Integration (secret-in-trace):** a full escalation and approval with a synthetic `client_secret`, bearer token, PEM block and signature finds none of them, nor the message text, anywhere in the exporter buffers.
  - **Integration:** `LANGFUSE_HOST` on a blackholed loopback port still gives `200` within the request timeout. This needs a short client timeout (CLAUDE.md, refused loopback ports can hang here).
  - **Regression:** P4/P7 MCP suites unchanged.
- **Step 7:** **Unit:** one fixture per score value. **Integration:** a run with an `APPROVAL_INVALID` outcome gives exactly one `incident_event`; a forced API crash gives an `api-error` trace with no message text.
- **Step 8:**
  - **Unit:** every marker category matches and a plain spec sentence does not; evidence fenced in the prompt with `sources` unchanged; answer fenced with the limitation added and empty findings dropped; `answer_echo` set before fencing.
  - **Integration:** `test_injection.py` passes all 10 gate checks in CI mode; `scan_markers.py` as a subprocess prints counts and no chunk text.
  - **Regression:** `eval_agent.py` CI mode passes.
- **Step 9:**
  - **Database (`db`):** rows just inside and just outside each period; `awaiting_approval` checkpoints kept; no foreign-key error; dry run deletes nothing.
  - **Integration (`db`):** `purge_retention.py` as a subprocess, plus `--dry-run`; the output has no DSN.
- **Step 10:** evidence only (the alert state screenshots and the run IDs, in the report). No automated test, because Langfuse's alert evaluation runs inside its own worker on its own schedule.
- **Step 11:** evidence only (live outputs pasted in the report).
- **Step 12:** `test_repo_hygiene.py` over the new docs, which are staged before running.
- **Step 13 (always row):** the agent-config eval job (`.github/workflows/agent-config-eval.yml`, verified present) runs on the `CLAUDE.md` change.

## Agent configuration changes

- **`CLAUDE.md`** (step 13), merged and append-only where the re-run rules say so:
  - Project status: "Memory and Langfuse arrive in P8" becomes a line naming `app/memory/` and `app/observability/`.
  - `## Commands`: `purge_retention.py` (with `--dry-run`) and `scan_markers.py`.
  - `## Common mistakes`: appended with whatever this build actually trips over. Nothing is pre-written.
  - `## Test scope`: unchanged, because every matched row is already listed.
- **`.claude/hooks/deploy-gate.sh` and `.claude/hooks/eval.sh`** (Phase 5): ask before running `scripts/purge_retention.py` without `--dry-run`. It deletes stored data, the same reasoning as `purge_audit.py`. `eval.sh` gains one act and one quiet pair. Each is its own diff and its own `approve write`.
- **Subagents:** none. `.claude/agents/` doesn't exist, and P8 has no recurring task that needs one.
- **CI:** none. The new tests run in the existing `ci.yml` test job, `agent-config-eval` exists (verified in Phase 4), and the weekly `pip-audit` covers the new dependency.

## Rollback

Revert the merge commit. `0003` adds tables only, so it needs no data migration back. `DROP TABLE` of the two memory tables, run as the owner, removes them, and the `app` grants go with the tables. Set `observability.enabled: false` to stop tracing without a revert. Traces already in the local Langfuse are removed only with its volume.
