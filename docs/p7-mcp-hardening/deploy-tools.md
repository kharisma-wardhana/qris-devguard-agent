# Deploy, status and rollback: p7-mcp-hardening
*Author: Kharisma Wardhana. Stage: Deploy. Written: 2026-11-11. Via /aspec:make-it-simple-v2.*

These tools are documented only. Nothing here is wired to a credential beyond the local, loopback-only PostgreSQL, API and MCP server.

## This feature

P7 changes how the services connect, not what they serve. The API and MCP server use three least-privilege PostgreSQL roles, every MCP call is audited, both servers rate-limit and size-limit, and outbound destinations are checked at start-up. "Deploying" it means the roles and the audit table exist and both services start on their role DSNs.

| Action | How | Who |
|---|---|---|
| Deploy (env) | `uv run python scripts/init_env.py --add-missing`. It appends `DEVGUARD_APP_DB_PASSWORD`, `DEVGUARD_TICKET_DB_PASSWORD` and `DEVGUARD_AUDIT_DB_PASSWORD` with generated values, leaves every existing line byte-identical, and prints key names only | Developer |
| Deploy (schema and roles) | `podman compose up -d postgres`, then `uv run python scripts/db_migrate.py` as the owner. It applies `0002_tool_audit_logs.sql`, then creates or alters the three roles and their grants, and sets passwords from `.env` without printing them. Re-running it changes nothing | Developer |
| Deploy (run) | `uv run python scripts/mcp_server.py`, then `uv run python scripts/api_server.py`. Both run the egress check and refuse to start with a disallowed destination, a non-loopback host, or a `DATABASE_URL` without their role password. The message names the setting, never its value | Developer |
| Status | `GET /health/ready`. `uv run python scripts/eval_security.py` re-measures the §22.5 suite and the §13.4 checklist into `security-report.md` (needs Postgres, or the database cases read NOT RUN). Audit rows: `select phase, tool, outcome, count(*) from tool_audit_logs group by 1, 2, 3` as the owner (`devguard_audit` cannot read its own table) | Developer |
| Retention | `uv run python scripts/purge_audit.py` deletes audit rows older than `audit.retention_days` (90) and prints the count. The deploy-gate hook asks before an agent runs it | Developer |
| Rollback (code) | `git revert` the merge or the step commits. P4–P6 behaviour returns, with the services on the owner role again | Human (repo owner) |
| Rollback (data) | Local and synthetic only: `DROP OWNED BY` then `DROP ROLE` for each of `devguard_app`, `devguard_ticket` and `devguard_audit` (as `app/db/roles.py` `drop()` does for test roles), `DROP TABLE tool_audit_logs`, and delete the `0002` row from `schema_migrations`. The `0001` tables are untouched. The three password lines in `.env` can stay | Human (repo owner) |

## Scoped MCP tools

The deploy/status/rollback tools proposed by earlier features still apply. P7 proposes two more, **documented, not wired**:

| Tool | Scope it would need | Never |
|---|---|---|
| `audit_lookup` (read-only) | Return the audit rows for one `correlation_id` or `run_id`: tool, outcome, phase, duration and time. It needs a fourth, read-only audit role, because `devguard_audit` is insert-only by design | Return other subjects' rows to a non-admin, page through the whole table, write or delete anything, or hold `devguard_audit`'s credentials |
| `security_report_status` (read-only) | Read the generated block of `docs/p7-mcp-hardening/security-report.md` and report each §13.4 control's status and the generation date | Run the suite, regenerate the report, or change a case or a cap |

Purging audit rows and changing grants stay human actions through the developer commands above. Neither must ever be exposed as an agent tool, because an agent able to delete its own audit trail or widen its own grants would void the §13.4 auditability and least-privilege controls.
