# Spec: Agent runs survive a crash in PostgreSQL, and the one side effect (a sandbox ticket) happens only after a bound, single-use human approval, exactly once
*Author: Kharisma Wardhana. Stage: Design. Written: 2026-11-11. Via /aspec:make-it-simple-v2.*
*Implements: docs/p6-checkpoint-hitl/intent.md*

## Behaviour

**Threads.**
- `POST /api/v1/threads` creates a thread owned by the caller and returns its `thread_id`.
- `POST /api/v1/threads/{thread_id}/messages` (body `{message, context}`, the same shape as `/chat`) runs the agent on that thread. It answers with `thread_id`, `run_id`, `status` and `answer`:
  - `status` is `completed` or `awaiting_approval`;
  - when it is `awaiting_approval`, the answer includes an `approval` block with the approval ID, the action summary and the expiry time.
- `GET /api/v1/threads/{thread_id}` returns the thread's status, the latest answer and any pending approval ID.
- Only the thread's owner can use these routes. Anyone else gets 404, so the thread's existence isn't revealed (T11).
- `POST /api/v1/chat` keeps working as before: a one-shot run on a new thread.

**Escalation (Journey D).**
1. A request to create or escalate a ticket (risk `high`) runs the normal read path first: search, plus any requested validator.
2. The agent composes its diagnosis.
3. The agent drafts a ticket from sanitized material only:
   - **title:** at most 200 characters;
   - **body:** the sanitized question and the diagnosis summary;
   - **evidence IDs:** citation IDs and validator references;
   - **priority:** `low`, `medium` or `high`.
4. The run pauses at a LangGraph interrupt. A pending approval is stored, bound to:
   - the exact action (`create_support_ticket` plus the normalized draft);
   - the requester;
   - the thread;
   - an expiry time (`approval_ttl_minutes`, 30).
5. Nothing has been written yet.

**Deciding.**
- `GET /api/v1/approvals/{approval_id}` shows what is being approved: the exact action and target, the sanitized parameters and evidence, the expected side effect ("one row in the local sandbox ticket table"), the requester, the expiry time and the status. The requester and any approver role can view it.
- `POST /api/v1/approvals/{approval_id}/decision` takes `{decision: approve|reject, reason?}`.
  - Only `support_engineer` and `admin` may decide (`configs/security.yaml` `approver_roles`).
  - The approver must not be the requester (four-eyes).
  - The approval must be pending and unexpired.
  - An approve resumes the run, which creates the ticket and returns it. A reject resumes it to a final "rejected, nothing created" answer.
  - A decision on an expired approval returns 409 `APPROVAL_EXPIRED`, marks the approval expired, and creates nothing.

**Exactly once.**
- The ticket is created by the MCP tool `create_support_ticket`. Before writing, the tool re-checks that the approval:
  - exists and is `approved`;
  - is unexpired;
  - belongs to the calling user and to this thread;
  - matches the hash of the exact payload it was given;
  - has not been used before.

  It inserts under a unique `idempotency_key`.
- These all produce no new ticket:
  - replaying the decision;
  - resuming again;
  - calling the tool with a changed payload, another user's approval, or an expired or used approval;
  - a crash after the insert but before the next checkpoint.

  A repeat with the same approval and payload returns the existing ticket, marked `already_created`.
- `POST /api/v1/threads/{thread_id}/resume` resumes a run that crashed after the decision was recorded. It does the same thing as above, exactly once.

**Survives restarts.** Graph state lives in PostgreSQL. After the API process is killed and started again:
- `GET` on the thread shows the same pending approval and run context;
- a decision made after the restart completes the original run.

**Flow 1 fix.** Before the model sees them, evidence chunks are ordered as follows, with fused rank breaking ties:
1. chunks from the endpoint's own section (from `endpoint_id`, or implied by the signature profile);
2. other chunks from the requested API family's documents;
3. everything else.

The live flows are then re-measured.

## Architecture and the reason for it

This conforms to `docs/tech.md` and `docs/structure.md` (TRD §24 adds `app/db/`). It extends `app/agent/`, `app/api/` and `app/mcp_server/`.

- **Checkpointer: `langgraph-checkpoint-postgres` 3.2.0** (MIT), using `AsyncPostgresSaver` over a `psycopg_pool` 3.3.3 `AsyncConnectionPool`. It creates its own tables through `setup()`, versioned by its `checkpoint_migrations` table.
  - *Reason:* TRD §11.4 calls for the official durable checkpointer with interrupt/resume support.
  - The `InMemorySaver` stays the default when no database is configured (the CI-mode harness and `eval_agent.py` CI mode), so CI keeps running without PostgreSQL.
- **Our schema: plain versioned SQL** in `app/db/migrations/NNNN_*.sql`, applied in order by `scripts/db_migrate.py`, which records each in a `schema_migrations` table. *Reason:* three tables don't need a migration framework, and the files are reviewable.
  - **`agent_threads`**: `thread_id`, `owner_id`, `status`, `last_run_id`, `created_at`, `updated_at`.
  - **`approval_requests`**: `approval_id`, `thread_id`, `run_id`, `requester_id`, `action_type`, `action_payload` (sanitized JSON), `action_hash`, `status`, `approver_id`, `decision_reason`, `decided_at`, `expires_at`, `executed_at`, `ticket_id`, `created_at`.
    - Status moves `pending → approved | rejected | expired`, then `approved → executed`.
    - A unique partial index on `(thread_id, action_hash) WHERE status = 'pending'` keeps re-creation idempotent.
  - **`sandbox_tickets`**: `ticket_id`, `idempotency_key UNIQUE`, `approval_id`, `thread_id`, `requester_id`, `title`, `body_sanitized`, `evidence_ids`, `priority`, `status`, `created_at`.
  - Every table holds only sanitized text, IDs and hashes (TRD §17).
- **Hashes:**
  - `normalized_payload = json.dumps({title, body, evidence_ids (sorted, unique), priority}, sort_keys=True, separators=(",",":"), ensure_ascii=False)`, with strings NFC-normalized and stripped.
  - `action_hash = sha256("create_support_ticket" + "\x1f" + normalized_payload)`
  - `idempotency_key = sha256(thread_id + "\x1f" + "create_support_ticket" + "\x1f" + normalized_payload)` (TRD §11.4)
  - Same payload, same key; any change gives a new hash, which invalidates the approval.
- **Graph changes (`app/agent/graph.py`):**
  - A high-risk request follows the read path (retrieve, validators, compose, policy). Then `draft_action` builds the ticket and goes to `request_human_approval`.
  - **`request_human_approval`** first gets or creates the pending approval. That step is idempotent, because LangGraph re-runs this node when it resumes. Then it calls `interrupt({...})`.
    - It skips straight to a draft-only answer, with no approval row, when the requester's role may not use `create_support_ticket` (the `security.yaml` matrix).
  - **`execute_approved_action`** reads the decision from the database, which is authoritative, never from the resume payload.
    - `approved`: it calls `create_support_ticket` through MCP with a token minted for the thread owner.
    - `rejected`, `expired`, or anything else: it executes nothing.
  - Then `memory_decision` → `finalize`. The final answer reports the ticket ID, or why nothing was created.
- **`create_support_ticket` MCP tool** (`app/mcp_server/`): a `GuardedTool` with a strict argument model:
  - `approval_id` (UUID);
  - `thread_id` (token pattern);
  - `title` (1–200);
  - `body` (1–4,000);
  - `evidence_ids` (at most 10, pattern-bound);
  - `priority` (enum);
  - `idempotency_key` (64 hex characters, verified against the recomputed key).

  In one transaction (`SELECT … FOR UPDATE` on the approval), it runs these checks in order:
  1. a ticket with this idempotency key and approval already exists: return it, `already_created`;
  2. the approval is missing or belongs to another thread or user: `APPROVAL_INVALID`;
  3. the hash differs: `APPROVAL_MISMATCH`;
  4. the approval has expired: `APPROVAL_EXPIRED`;
  5. the status is not `approved`, for example already executed: `APPROVAL_NOT_USABLE`;
  6. otherwise it inserts the ticket, sets the approval to `executed` with `executed_at` and `ticket_id`, and returns the new ticket.

  If the database is down, it returns `TICKET_STORE_UNAVAILABLE`. Roles come from the existing matrix (`developer`, `support_engineer`, `admin`). The tool is annotated as not read-only, not idempotent at the protocol level, and not open-world.
- **Database access:** one `DATABASE_URL` (the P1 `devguard` role) for the API, the agent and the MCP server.
  - The MCP server uses short sync psycopg connections inside its threaded handlers.
  - The API uses the async pool.
  - Per-tool least-privilege roles are deferred to P7; see Flagged concerns.
- **Windows event loop:** psycopg's async driver cannot run on the Proactor loop that is Windows' default. `scripts/api_server.py`, `scripts/db_migrate.py` and the test `conftest.py` set `WindowsSelectorEventLoopPolicy` on Windows. Linux is unaffected.
- **API** (`app/api/`): the new routes reuse the P5 bearer auth (API audience), the strict models and the §18 envelope. The new codes are `NOT_FOUND`, `FORBIDDEN`, `CONFLICT`, `APPROVAL_EXPIRED` and `SERVICE_UNAVAILABLE` (database down).
  - Every query filters by owner, or by approver role for approval decisions.
  - One log line per request, never message text.
- **Evidence ordering** (`app/agent/graph.py`, `rerank_evidence`): a stable sort using a new `configs/agent.yaml` block:
  - `endpoint_sections`: `access-token-b2b → snap-technical-security-standard 2.1.5`; `qr-mpm-generate → synthetic-qris-mpm-service-spec 2`; `qr-mpm-query → … 3`;
  - `profile_endpoints`: `snap-asymmetric-access-token-v1 → access-token-b2b`.

  The node still never runs the CPU cross-encoder.
- **Database tests** carry `@pytest.mark.db`. A fixture connects to `DEVGUARD_TEST_DATABASE_URL`, or failing that to `DATABASE_URL` from the environment or `.env`.
  - Each test session creates a throwaway schema (`p6test_<random>`) and drops it afterwards. Nothing touches the real tables.
  - Without a reachable database, every `db` test is **skipped with a reason**, so pytest reports `s`, never a pass. CI has no PostgreSQL (your decision at intake), so these tests skip there.
- **Config:** a new `database:` block in `configs/agent.yaml` holds `url_env: DATABASE_URL`, the pool size and the connect timeout. `configs/security.yaml` is read only (`approver_roles`, `approval_ttl_minutes`, the tool matrix).

## Data

- **New PostgreSQL tables** (local, loopback): `agent_threads`, `approval_requests`, `sandbox_tickets` and `schema_migrations`, plus LangGraph's `checkpoints`, `checkpoint_blobs`, `checkpoint_writes` and `checkpoint_migrations`.
- **Sensitivity:** sanitized synthetic text, IDs and hashes only. Graph state was already sanitized in P5. Ticket title and body are redacted again before storage.
- **Retention:** scope.md sets checkpoints to 30 days and audit events to 90 days. P6 does not enforce deletion yet; see Flagged concerns.
- **Logs and reports:** IDs, statuses and timings only, as in P5.

## Acceptance criteria

1. `scripts/db_migrate.py` applies `0001` on an empty schema and records it in `schema_migrations`. A second run applies nothing. The checkpointer's `setup()` creates its tables. Both run against a throwaway schema in tests.
2. Thread routes: create, message, read. Another user's `thread_id` gets 404 on every route, and an unknown ID gets 404.
3. A high-risk message on a thread returns `status: awaiting_approval` with an approval block. Exactly one pending `approval_requests` row exists for it, no ticket exists, and no write tool was called.
4. `GET /approvals/{id}` shows the action, the sanitized parameters, the evidence IDs, the expected side effect, the requester, the expiry time and the status, with no secret. A user who is neither the requester nor an approver gets 404.
5. Decisions:
   - a `developer` or `kb_maintainer` gets 403 `FORBIDDEN`;
   - the requester deciding their own approval gets 403, even as `support_engineer` or `admin`;
   - a decision on a non-pending approval gets 409;
   - a decision after `expires_at` gets 409 `APPROVAL_EXPIRED`, sets the status to `expired`, and creates no ticket.
6. An approve resumes the run and creates exactly one `sandbox_tickets` row. The answer reports its `ticket_id`, and the approval ends `executed`. A reject creates none, and the answer says so.
7. The tool refuses, with no ticket created, each of:
   - a payload that differs in any field: `APPROVAL_MISMATCH`;
   - another user's approval: `APPROVAL_INVALID`;
   - another thread's approval: `APPROVAL_INVALID`;
   - a pending, rejected or expired approval: `APPROVAL_NOT_USABLE` or `APPROVAL_EXPIRED`;
   - a wrong `idempotency_key`: `INVALID_ARGUMENT`.
8. Replaying an executed approval with the same payload returns the existing ticket, `already_created`. The ticket count stays 1. A unique-constraint test proves the database itself refuses a second row with the same key.
9. **Crash between insert and checkpoint** (fault injection): the tool inserts, then the graph raises before checkpointing. `POST /threads/{id}/resume` completes the run with the same `ticket_id`, and the count stays 1.
10. **Crash/resume (TRD §22.4)**, as an integration test with real subprocesses (API, MCP server, fake LLM, local PostgreSQL):
    1. escalate, giving `awaiting_approval`;
    2. **kill** the API process;
    3. start a new API process;
    4. `GET` the thread, which shows the same `run_id` and the pending approval;
    5. approve, which creates one ticket;
    6. approve again (409) and resume again (no new ticket).

    Exactly one ticket exists for that idempotency key. The test records thread, run and approval IDs and the ticket count.
11. No secret appears in any table, checkpoint or log after a run whose message and payload contained the configured secret patterns. The test checks with a database dump query.
12. Evidence ordering: for flow 1's context, a §2.1.5 chunk is the first evidence chunk whenever one was retrieved, and family-less requests keep fused order (unit). The live flows are re-measured in `docs/p6-checkpoint-hitl/agent-flow-report.md`, and flow 1's result is stated as measured.
13. Database tests are marked `db` and skipped with a reason when no database is reachable. The CI run shows them as skipped, not passed. Locally, against the Podman PostgreSQL, they run and pass.
14. The P5 behaviour holds: `/api/v1/chat`, the five CI-mode flows (`eval_agent.py`), and every existing test pass unchanged. With no database configured, the agent still runs on `InMemorySaver`, and a high-risk request falls back to P5's draft-only answer.

## Flagged concerns

- **Security: one database role for everything.** The API, the agent and the MCP ticket tool all use the P1 `devguard` owner role. TRD §13.3 ("least-privilege DB credentials per tool") and the P7 go-live checklist both call for per-tool roles, for example a ticket-writer role that can only insert tickets and update approvals. This is deferred to P7, because adding roles needs new secrets in an existing `.env` that `init_env.py` refuses to overwrite. *Decides:* Kharisma Wardhana.
- **Security: four-eyes and no team model.** The requester can never approve their own action, even as `admin`. But any `support_engineer` or `admin` can approve any thread's action: scope.md's "their team's threads" needs a team model that doesn't exist yet. *Decides:* Kharisma Wardhana.
- **Assurance: database tests don't run in CI** (your intake decision). The crash/resume proof, approval bypass and idempotency tests run only locally. A regression in them would not turn CI red. The PR will carry the pasted local run. *Decides:* Kharisma Wardhana (accepted at intake; restated here).
- **Retention not enforced.** Checkpoints (30 days) and approvals and tickets (90 days, as audit) accumulate. A deletion job is P8 (memory and retention) work. *Decides:* Kharisma Wardhana.
- **Windows-specific runtime change.** The selector event loop policy is set on Windows for the API, migration script and tests. It must not leak into code paths that need the Proactor loop; nothing in the repo uses subprocess pipes from inside the event loop. *Decides:* none further; recorded.
- **Privacy.** Ticket title and body come from sanitized text and are redacted again before storage. They could still hold personal data a user typed, so synthetic data only, by policy. *Decides:* none further.
- **UX and accessibility:** no UI; the approval interface is the JSON API (user decision at intake). None found.

## Out of scope

- A web approval page, notifications to approvers, and approval delegation.
- Per-tool database roles, rate limits, the audit table (`tool_audit_logs`), TLS and the §22.5 security report (P7).
- Retention jobs, long-term memory, and Langfuse traces of resumed runs (P8).
- GPU reranking. The flow 1 fix is evidence ordering only.
- Any write target other than `sandbox_tickets`, and any ticket update, close or delete.
- PostgreSQL in CI.

## Resolved questions

- **Checkpointer version and migrations?** `langgraph-checkpoint-postgres==3.2.0` (MIT) with `AsyncPostgresSaver`, whose tables come from its own versioned `setup()`. Our three tables use plain versioned SQL files applied by `scripts/db_migrate.py` with a `schema_migrations` table. No migration framework.
- **Action hash and idempotency key?** Both are built from the same normalized payload `{title, body, evidence_ids (sorted, unique), priority}`. The `action_hash` is over action type plus payload. The `idempotency_key` is over thread ID, action type and payload, following the TRD formula. Any payload change changes both.
- **How does the approval reach the tool, and with which credential?** The tool gets the `approval_id` and the full payload. It recomputes the hash and key, then checks the approval row under `SELECT … FOR UPDATE`. It uses the shared `DATABASE_URL` role in P6; per-tool roles are P7 (flagged).
- **A crash between the insert and the next checkpoint?** On resume, the tool is called again with the same approval and payload. It finds the existing ticket by idempotency key and approval, and returns it as `already_created`. The graph then finishes normally.
- **Who may decide?** `approver_roles` (`support_engineer`, `admin`) and never the requester (four-eyes). There is no team scoping yet (flagged).
- **How does the crash test restart?** It kills the API subprocess (`kill`, not a graceful stop) and starts a new one on the same database. The new process recovers state only from PostgreSQL.
- **Evidence ordering when no endpoint is given?** The endpoint is implied by the signature profile (`snap-asymmetric-access-token-v1` → `access-token-b2b` → SNAP §2.1.5). Without an endpoint or profile, chunks from the API family's documents come first. With neither, fused order is kept.
- **Database tests in CI?** A `db` marker plus a fixture that skips with a reason when no database is reachable. Skips show as `s`; nothing is reported as passed. No CI job runs them, per your intake decision.
