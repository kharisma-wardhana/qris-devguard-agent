# Deploy, status and rollback: p6-checkpoint-hitl
*Author: Kharisma Wardhana. Stage: Deploy. Written: 2026-11-11. Via /aspec:make-it-simple-v2.*

These tools are documented only. Nothing here is wired to a credential beyond the local, loopback-only PostgreSQL, API and MCP server.

## This feature

P6 adds durable state: graph checkpoints, threads, approvals and sandbox tickets, all in the local PostgreSQL. "Deploying" it means the schema is migrated and the API starts in PostgreSQL mode.

| Action | How | Who |
|---|---|---|
| Deploy (schema) | `podman compose up -d postgres`, then `uv run python scripts/db_migrate.py`. It applies `app/db/migrations/` once each and LangGraph's checkpoint tables, and is safe to re-run | Developer |
| Deploy (run) | `uv run python scripts/mcp_server.py` (the ticket store comes from `DATABASE_URL`), then `uv run python scripts/api_server.py`. It must print "PostgreSQL checkpointer, approvals on"; otherwise the database wasn't reachable or migrated, and it fell back to in-memory with approvals off | Developer |
| Status | `GET /health/ready`. `GET /api/v1/threads/{id}` (owner). `GET /api/v1/approvals/{id}` (requester or approver). `uv run pytest -m db` proves the approval, idempotency and crash/resume rules against a throwaway schema | Developer |
| Decide | `POST /api/v1/approvals/{id}/decision` with a `support_engineer` or `admin` token for a different user than the requester. Mint one with `scripts/mint_dev_token.py --audience api` | Human approver |
| Recover after a crash | Restart the API. If a decision was recorded but the run didn't finish, `POST /api/v1/threads/{id}/resume`. The ticket tool returns the existing ticket rather than creating a second | Developer / approver |
| Rollback (code) | `git revert` the merge or the step commits. `/api/v1/chat`, the P4 read tools and the validators work without P6 | Human (repo owner) |
| Rollback (data) | Local and synthetic only: `DROP TABLE sandbox_tickets, approval_requests, agent_threads, schema_migrations` and the LangGraph `checkpoint*` tables, or `podman compose down -v` to wipe the whole local stack | Human (repo owner) |

## Scoped MCP tools

The deploy/status/rollback tools proposed by earlier features still apply. P6 proposes two more, **documented, not wired**:

| Tool | Scope it would need | Never |
|---|---|---|
| `approvals_pending` (read-only) | List pending approvals: ID, thread, requester, action type, expiry. No payload text. For an approver dashboard | Decide, resume, or return ticket or payload text |
| `db_migration_status` (read-only) | Read `schema_migrations` and `checkpoint_migrations`; report applied versions versus the files in `app/db/migrations/` | Apply a migration, drop or alter anything, or print a DSN |

Deciding an approval stays a human action through the API. It must never be exposed as an agent tool, because an agent able to approve its own drafts would void TRD §15.2.
