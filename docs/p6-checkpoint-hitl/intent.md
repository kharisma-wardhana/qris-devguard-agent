# Intent: Agent runs survive a crash in PostgreSQL, and the one side effect (a sandbox ticket) happens only after a bound, single-use human approval, exactly once
*Author: Kharisma Wardhana. Stage: Plan. Written: 2026-11-10. Via /aspec:make-it-simple-v2.*

## Problem statement

P5 left an agent that diagnoses and drafts, but cannot act and cannot survive a restart. The PRD's P6 exit criterion, *crash/resume test passes; no duplicate action*, isn't met:

- **State dies with the process.** The graph compiles with an in-memory checkpointer. A crash or restart loses every run, and a thread can't be continued or resumed (TRD §11.4).
- **The only side effect has no path.** A request to escalate gets a draft and the note "approval arrives in P6". Several pieces don't exist yet:
  - the `create_support_ticket` tool;
  - the sandbox ticket table;
  - an approval record;
  - an interrupt that waits for a human (TRD §15.2, Journey D).
- **Nothing enforces the approval rules.** CLAUDE.md requires:
  - an approval bound to the exact action payload, the user and the thread;
  - an expiry, single use, and invalidation on any payload change;
  - `idempotency_key = hash(thread_id + action_type + normalized_action_payload)` with a unique database constraint.

  Threats T4 (approval bypass, replay, expired or changed payload) and T5 (duplicate side effect on resume or retry) are still unproven.
- **Thread and approval routes are missing.** TRD §18 lists them; only the P5 one-shot `/api/v1/chat` exists.
- **P5's live flow 1 still fails.** SNAP §2.1.5 was retrieved but not cited. The agreed P6 follow-up is evidence ordering, then a live re-measure.

## Proposed outcome

- **Durable runs.** The graph compiles with LangGraph's PostgreSQL checkpointer against the local Postgres from P1.
  - Threads are owned by the caller and recorded in `agent_threads`.
  - Restarting the API process loses nothing: a thread can be read, continued, and resumed from its last checkpoint.
- **Human approval before the side effect.**
  - A high-risk request produces a draft ticket and stops at a LangGraph interrupt (`request_human_approval`). The interrupt persists an `approval_requests` row bound to the action hash, requester, thread and expiry (`approval_ttl_minutes`).
  - An authorized approver (`support_engineer` or `admin`; `kb_maintainer` excluded) approves or rejects the exact action through the API. Rejection or expiry executes nothing.
- **Exactly-once execution.**
  - On approval, the graph resumes into `execute_approved_action`, which calls a new MCP tool, `create_support_ticket`.
  - The tool re-checks the approval (exact payload hash, user, thread, unexpired, unused) and inserts into `sandbox_tickets` under a unique `idempotency_key`.
  - A replayed approval, a changed payload, an expired approval, another user's approval, or a resume after a crash never creates a second ticket.
- **The TRD §18 routes,** all scoped to the caller's own threads:
  - `POST /api/v1/threads`
  - `POST /api/v1/threads/{id}/messages`
  - `GET /api/v1/threads/{id}`
  - `POST /api/v1/threads/{id}/resume`
  - `GET /api/v1/approvals/{id}`
  - `POST /api/v1/approvals/{id}/decision`

  `/api/v1/chat` stays as a one-shot shortcut. The API is the approval interface; there is no web UI.
- **The TRD §22.4 crash/resume test.** Interrupt before the ticket, stop the API process, start it again, resume, and approve. Exactly one ticket exists, and the thread shows the original run context.
- **The flow 1 fix.** The evidence block is ordered by the requested API family, then the endpoint's own section, before the model sees it. The live flows are re-measured in one short pod session.

## Affected users and systems

- Kharisma Wardhana: approves or rejects actions as `support_engineer`/`admin` in tests and the demo, and runs the one live session.
- `app/agent/`: graph with the PostgreSQL checkpointer, `request_human_approval` (interrupt) and `execute_approved_action`; evidence ordering.
- `app/api/`: the thread and approval routes, owner scoping and resume.
- `app/mcp_server/`: the `create_support_ticket` write tool with its approval and idempotency checks.
- `app/db/` (new): schema migrations and queries for `agent_threads`, `approval_requests` and `sandbox_tickets`.
- Local PostgreSQL 17 (P1, `127.0.0.1:5432`): new schema; the checkpoint tables are created by the LangGraph migration.
- `configs/`: database and approval settings. `security.yaml`'s `approver_roles` and `approval_ttl_minutes` are read, not changed.
- `pyproject.toml` / `uv.lock`: the LangGraph PostgreSQL checkpointer package, pinned exactly.
- CI: **no PostgreSQL in CI** (user decision). The database tests skip there and run locally against the Podman Postgres; the rest of the suite runs as before.
- Downstream: P7 (hardening and the full §22.5 security set), P8 (memory, Langfuse, audit), P9 (final evaluation).

## Constraints

- **Deadline:** P6 is done by 2026-11-13. All of P1–P9 must be done by 2026-11-20.
- **GPU:** at most one short pod session, to re-measure the live flows and demo the approval path. The pod is terminated after it.
- **The only write target** is the local `sandbox_tickets` table. No payment, refund or production tool exists or is added.
- **Approvals** are bound to the exact action payload, the requesting user and the thread, expire after `approval_ttl_minutes` (30), and are single-use. Any payload change invalidates them, and approver roles come from `configs/security.yaml`.
- **Idempotency** is enforced at the action boundary by a unique database constraint, never by thread-ID replay alone (TRD §11.4).
- **No secrets** in checkpoints, tables or logs. Only sanitized values, fixture IDs and hashes are persisted (CLAUDE.md; TRD §17).
- **State isolation:** every thread, approval and ticket lookup is scoped by owner (T11).
- **Local and loopback only:** PostgreSQL on `127.0.0.1`, least privilege where the local setup allows.
- **CI:** the database tests skip without PostgreSQL. Everything else keeps passing in the existing jobs unchanged.
- **Must not break:** P2–P5 behaviour and all existing tests. `/api/v1/chat` keeps working.

## Open questions

- Which `langgraph-checkpoint-postgres` version, and how are its tables created and versioned alongside our own schema? Does the app need its own migration tool, or plain versioned SQL files with a schema-version table?
- How exactly is the action hash defined (normalization of the ticket payload), and how does it relate to the idempotency key? Which fields form the ticket payload: title, body, evidence IDs, priority?
- How does the approval reach the MCP tool? An approval ID the tool looks up in the database, or a signed approval token? Which database role or credential does the MCP server use for the write, given "least-privilege DB credentials per tool" in the TRD?
- What happens on a crash between the ticket insert and the graph's checkpoint after it? How does resume detect that the ticket already exists and report it rather than fail?
- Who may decide an approval? Must the approver differ from the requester (separation of duties), and can the requester approve their own escalation if they are a `support_engineer`?
- How does the crash/resume test stop and restart the agent? Killing the API subprocess, or a fresh graph and process against the same database? What evidence does it record?
- How does evidence ordering choose "the endpoint's own section" when no endpoint is given (flow 1 has only `api_family: snap` and a signature test case)?
- How are the database tests skipped in CI and selected locally (a marker, or an environment variable), so that a skipped test is never reported as passed?
