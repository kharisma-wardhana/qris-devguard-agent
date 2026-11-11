# Plan: Agent runs survive a crash in PostgreSQL, and the one side effect (a sandbox ticket) happens only after a bound, single-use human approval, exactly once
*Author: Kharisma Wardhana. Stage: Build. Written: 2026-11-11. Via /aspec:make-it-simple-v2.*
*Implements: docs/p6-checkpoint-hitl/spec.md*

## Affected files

New: application code
- `app/db/__init__.py`: package.
- `app/db/connect.py`: resolves `DATABASE_URL` (environment, then `.env`; `postgresql+psycopg://` is normalised), opens sync connections, and sets the Windows selector-loop policy (`use_selector_loop()`).
- `app/db/migrations/0001_threads_approvals_tickets.sql`: `agent_threads`, `approval_requests`, `sandbox_tickets` and indexes (including the unique `idempotency_key` and the unique pending `(thread_id, action_hash)`).
- `app/db/migrate.py`: applies migrations in order and records them in `schema_migrations`, idempotently.
- `app/db/actions.py`: the ticket payload normalization, `action_hash` and `idempotency_key`. Shared by the agent and the MCP tool.
- `app/db/store.py`: sync, owner-scoped queries:
  - **threads:** create, get, set status;
  - **approvals:** get-or-create pending, get, decide, expire;
  - **tickets:** create-under-approval in one transaction, with the `FOR UPDATE` checks; count by key.

New: scripts
- `scripts/db_migrate.py`: applies our SQL migrations and the LangGraph checkpointer `setup()`. Prints applied and skipped; never prints the DSN.

New: tests
- `tests/conftest.py`: registers the `db` marker's fixture, sets the Windows selector policy, and provides `db_url` (a throwaway `p6test_<random>` schema, dropped after the session). It skips with a reason when no database is reachable.
- `tests/unit/test_db_migrate.py`
- `tests/unit/test_db_store.py`
- `tests/unit/test_ticket_tool.py`
- `tests/unit/test_agent_hitl.py`
- `tests/unit/test_api_threads.py`
- `tests/integration/test_db_migrate_cli.py`
- `tests/integration/test_crash_resume.py`: real API, MCP and PostgreSQL; kills the API process.

New: docs
- `docs/p6-checkpoint-hitl/agent-flow-report.md`: the live re-measure.

Edited
- `pyproject.toml` / `uv.lock`: `langgraph-checkpoint-postgres==3.2.0`, `psycopg-pool==3.3.3`, and the `db` marker in `[tool.pytest.ini_options]`.
- `configs/agent.yaml`: `database:` (url_env, pool size, connect timeout), `evidence_order:` (`endpoint_sections`, `profile_endpoints`) and the ticket draft limits.
- `app/mcp_server/config.py`: `TOOL_NAMES` gains `create_support_ticket`.
- `app/mcp_server/models.py`: `TicketArgs`.
- `app/mcp_server/tools.py`: the `create_support_ticket` handler, plus a database-connection factory in `ToolContext`.
- `app/mcp_server/server.py`: annotations per tool (the write tool is not read-only).
- `app/agent/graph.py`:
  - high-risk requests go read path → `draft_action` → `request_human_approval` (interrupt) → `execute_approved_action`;
  - evidence ordering in `rerank_evidence`;
  - `Agent.run(thread_id=…)`, `Agent.resume(thread_id)` and `Agent.state(thread_id)`;
  - an optional approval store (no store means P5 draft-only).
- `app/agent/answer.py`: awaiting-approval, ticket-created, rejected/expired and already-created answers.
- `app/agent/state.py`: `approval`, `ticket` and `draft_action` fields.
- `app/agent/harness.py`: an optional database-backed tool context (the MCP harness can serve the write tool against a test schema).
- `app/api/app.py`: thread, approval, decision and resume routes; store injection; database-down 503 envelope.
- `app/api/models.py`: thread, message, decision and approval-view models.
- `scripts/api_server.py`: wires the PostgreSQL checkpointer and store when the database is reachable, otherwise in-memory with a printed note; selector loop.
- `scripts/mcp_server.py`: passes the database factory to the tool context; selector loop is not needed (sync).
- `tests/unit/test_mcp_tools.py`, `tests/integration/test_mcp_server.py`: regression tests. The tool list goes from 4 to 5 by design, the read-only annotation stays on the four read tools, and the write tool has its own role-matrix row.
- `docs/threat-model.md`: T4, T5 and T11 gain P6 evidence. T6 adds the database and checkpoints.
- `docs/tech.md`, `docs/structure.md`, `README.md`, `docs/index.md`
- `CLAUDE.md`: see Agent configuration changes.

Not changed: `configs/security.yaml` (read only), `app/validators/*`, `app/rag/*`, `.claude/*`, `.github/*`.

## Work sequence

1. **Dependencies, config, the `db` test fixture.** Pin the two packages, add the `configs/agent.yaml` blocks, the marker, `tests/conftest.py`, and `app/db/__init__.py` + `connect.py`. (spec AC #13)
2. **Schema, migrations, actions, store.** Write `0001_*.sql`, `migrate.py`, `actions.py`, `store.py` and `scripts/db_migrate.py`. (AC #1, #8 unique constraint, #11 storage)
3. **The `create_support_ticket` MCP tool.** Edit `config.py`, `models.py`, `tools.py` and `server.py`, plus the regression updates (4 → 5 tools). (AC #7, #8)
4. **Evidence ordering.** `rerank_evidence` plus the config. (AC #12, unit)
5. **Graph HITL and the PostgreSQL checkpointer.** Edit `graph.py`, `answer.py`, `state.py` and `harness.py`. (AC #3, #6, #9, #14)
6. **API routes.** Edit `app/api/*` and `scripts/api_server.py` / `mcp_server.py`. (AC #2, #4, #5)
7. **Crash/resume and the secrets dump.** Write `tests/integration/test_crash_resume.py`. (AC #10, #11)
8. **Live re-measure.**
   - **You:** start the pod and the tunnel (GPU spend).
   - **Me:** start Postgres, Qdrant, the MCP server and the API; run `scripts/eval_agent.py --live --api-smoke`; demo one approval through the API; write `agent-flow-report.md`.
   - **You:** terminate the pod. (AC #12)
9. **Threat model and steering docs.** (spec Flagged concerns)
10. **`CLAUDE.md` merge.** Shown as a diff, written only after `approve write`. (Agent configuration)

Steps 3 and 4 are independent and share no file. Step 3 needs step 2's store. With a two-day budget, I'll run them sequentially in this session.

## Test coverage

Matched rows from `test-scope-by-stage.md`:
- **any database** (new this feature): a regression test for every schema change and every query or stored-data behaviour;
- **backend service / script entry point:** integration tests at the entry points;
- **roles/permissions:** authentication and authorization integration tests;
- **existing project:** regression tests;
- **always:** the agent-config eval.

Not matched: UI/e2e (no screen; the API is the approval interface).

Database tests carry `@pytest.mark.db`. They run locally against the Podman PostgreSQL in a throwaway schema, and skip with a reason in CI (intake decision).

- **Step 1:** unit tests check that the config loads, `connect.dsn()` normalises `postgresql+psycopg://` and never appears in an exception message, the fixture skips with a reason when unreachable, and the selector policy is set on Windows.
- **Step 2:**
  - **Database regression (`db`):** `0001` applies on an empty schema; a re-run applies nothing; the `schema_migrations` row exists; a duplicate `idempotency_key` insert is refused by the constraint; a second pending approval for the same `(thread_id, action_hash)` is refused; store queries are owner-scoped (another owner gets `None`).
  - **Unit:** normalization (sorting, NFC, stripping, unique evidence IDs); any field change changes `action_hash` and `idempotency_key`; same input gives the same key; the key includes `thread_id`.
  - **Integration:** `scripts/db_migrate.py` as a subprocess applies and then reports nothing to apply, prints no DSN, and gives a one-line error when the database is unreachable.
- **Step 3:**
  - **Database (`db`), the tool against a test schema:**
    - success creates one ticket and sets the approval to `executed`;
    - same payload again gives `already_created` and still 1 ticket;
    - a changed field gives `APPROVAL_MISMATCH`;
    - another user or another thread gives `APPROVAL_INVALID`;
    - pending, rejected or executed-with-a-new-payload gives `APPROVAL_NOT_USABLE`;
    - expired gives `APPROVAL_EXPIRED`;
    - a wrong `idempotency_key` gives `INVALID_ARGUMENT`;
    - database down gives `TICKET_STORE_UNAVAILABLE`.
  - **Authorization (unit):** the role matrix for `create_support_ticket`, where `kb_maintainer` gets `FORBIDDEN`.
  - **Regression:** five tools listed; the four read tools keep `read_only_hint`; the write tool doesn't have it.
- **Step 4:** unit tests: flow 1's context puts §2.1.5 first when it was retrieved; `qr-mpm-generate` puts section 2 first; family-only puts family documents first; with neither, fused order is kept; ties keep fused rank.
- **Step 5:**
  - **Unit (no database):** P5 behaviour unchanged. High risk without a store gives draft-only, and the existing `test_agent_graph.py` passes unchanged.
  - **Database (`db`), graph with the PostgreSQL checkpointer and the database-backed harness:**
    - high risk gives `awaiting_approval`, one pending row, no ticket;
    - approve then resume gives one ticket and the approval `executed`;
    - reject gives no ticket;
    - expired gives no ticket;
    - re-running `request_human_approval` on resume creates no second approval;
    - **fault injection:** the tool inserts, then the graph raises before checkpointing, then resume returns the same `ticket_id` and the count stays 1;
    - `kb_maintainer` escalation gives draft-only and no approval row.
- **Step 6:**
  - **Unit, `TestClient` + database (`db`):** thread create, message and read; another owner gets 404; approval view fields; decision rules (developer and kb_maintainer 403, self-approve 403, not pending 409, expired 409 `APPROVAL_EXPIRED`); approve gives a ticket in the answer; resume after a recorded decision; database down gives the 503 envelope.
  - **Regression:** `/api/v1/chat` and the five CI-mode flows unchanged.
- **Step 7:**
  - **Integration (`db`), with the real API, MCP and PostgreSQL:** the TRD §22.4 sequence including `kill`, a new API process, `GET` showing the same `run_id`, approve, then a second approve (409) and a second resume (no new ticket); the ticket count for the key is exactly 1.
  - **Secrets dump:** after a run carrying the secret patterns, a query over every new table and the checkpoint tables finds none of them.
  - **Auth integration:** an approver token from another user can decide; the requester's own approver-role token cannot.
- **Step 8:** no new test type. It runs `eval_agent.py --live --api-smoke`, and the output is pasted into the report.
- **Step 9:** the existing hygiene tests.
- **Step 10:** the agent-config eval in Phase 4.
- **Every step:**
  - stage new files, then run `uv run pytest`, gated on its own exit code;
  - check that local database tests ran (not skipped) when Postgres is up;
  - edit no existing test except the two regression files named above.

## Agent configuration changes

- **`CLAUDE.md` (step 10, its own diff and `approve write`):**
  - `## Commands`: `db_migrate.py`, and how to run the database tests (`uv run pytest -m db`, Postgres up).
  - `## Test scope`: the existing database row already applies; append only if a new kind of row is needed (for example "database tests skip in CI; paste the local run").
  - `## Common mistakes`: only lessons actually learned.
  - `## Project status`: add `app/db/`.
- **Subagents, hooks, CI:** none.
  - No CI job is added (intake decision).
  - The deploy gate already asks on `tunnel.sh`.
  - The deny-list already covers `.env`.

## Rollback

Revert the PR. Locally, `DROP TABLE` the three P6 tables, `schema_migrations` and the LangGraph checkpoint tables. They hold only synthetic sandbox data. `/api/v1/chat` keeps working in-memory without them. P4 tools and validators are unaffected apart from the added write tool, which the revert removes.
