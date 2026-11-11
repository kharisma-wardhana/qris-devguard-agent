# Plan: Every TRD §13.4 go-live control has recorded evidence, and the §22.5 MCP security test set shows zero unauthorized side effects and no secret leakage
*Author: Kharisma Wardhana. Stage: Build. Written: 2026-11-11. Via /aspec:make-it-simple-v2.*
*Implements: docs/p7-mcp-hardening/spec.md*

## Affected files

New: application code
- `app/security/__init__.py`: package (TRD §24 layout).
- `app/security/ratelimit.py`: token-bucket limiter with an injectable clock and bounded keys, plus the pre-auth per-address ASGI middleware (429 envelope, `Retry-After`).
- `app/security/body_limit.py`: request-size ASGI middleware. It returns the 413 envelope for both declared and streamed (chunked) bodies.
- `app/security/egress.py`: start-up check of configured destinations against `egress_allowlist`. Hosts must be loopback literals or allowlisted names; private, link-local and metadata addresses are refused, and so are schemes other than http/https/postgresql.
- `app/db/migrations/0002_tool_audit_logs.sql`: the audit table (spec §Data) and its CHECK constraints.
- `app/db/roles.py`: the role and grant table, from the spec Behaviour §4 role table. Role names are parameterised, so tests use random throwaway names and never touch the real roles. Passwords are set through `psycopg.sql.Literal`.
- `app/db/audit.py`: audit writer (insert-only), purge (as the owner) and the outcome-code set.

New: scripts
- `scripts/purge_audit.py`: deletes audit rows older than `audit.retention_days` and prints the count.
- `scripts/eval_security.py`: runs the security suite with evidence capture on, and renders the §22.5 matrix and §13.4 table into `docs/p7-mcp-hardening/security-report.md`. `--live-injection` runs the 10 injection cases against real vLLM with harness tools.

New: datasets and fixtures
- `evals/datasets/security_cases.yaml`: one entry per §22.5 case (ID, §22.5 bullet, payload description, expected result).
- `evals/datasets/injection_cases.yaml`: the 10 injection cases (5 user-turn, 5 document-borne) with each case's benign baseline request.
- `tests/fixtures/injection/*.md`: synthetic planted documents for the five document-borne cases. Each carries the SYNTHETIC title marker.

New: tests
- `tests/unit/test_security_ratelimit.py`
- `tests/unit/test_security_body_limit.py`
- `tests/unit/test_security_egress.py`
- `tests/unit/test_db_roles.py`: `db`; one assertion per "may not" cell.
- `tests/unit/test_audit.py`: `db` plus unit tests.
- `tests/integration/test_security_suite.py`: the §22.5 cases against real API and MCP subprocesses. Database cases are marked `db`. Evidence is written only when `DEVGUARD_SECURITY_EVIDENCE` names a file.
- `tests/integration/test_injection.py`: the 10 cases in CI mode, plus the socket guard (SSRF) for an agent run.
- `tests/integration/test_purge_audit_cli.py`
- `tests/integration/test_mcp_audit.py`: (added in step 3) audit rows from the real MCP subprocess on the throwaway audit role: one row per outcome, the run ID, 401 refusals, and no secret-shaped argument in any row.
- `tests/integration/test_eval_security_cli.py`

New: CI and docs
- `.github/workflows/container-scan.yml`: a new workflow with one job, a Trivy action pinned by commit SHA over the 7 compose images. It runs on pull requests that touch `docker-compose.yml` or itself, weekly, and on manual dispatch. `continue-on-error: true`.
- `docs/p7-mcp-hardening/security-report.md`: rendered §22.5 matrix, §13.4 table and scan triage.
- `docs/p7-mcp-hardening/transport.md`: the TLS note for a future ingress (documented, not deployed).

Edited
- `configs/security.yaml`: `limits.max_request_bytes`, `limits.rate` (the spec Behaviour §2 values) and `audit.retention_days: 90`. The threat model is reviewed alongside, per the file's header rule.
- `configs/agent.yaml`: `database.roles` (app, ticket, audit: role name and password env key).
- `.env.example`: `DEVGUARD_APP_DB_PASSWORD`, `DEVGUARD_TICKET_DB_PASSWORD`, `DEVGUARD_AUDIT_DB_PASSWORD` (all `replace-me`).
- `scripts/init_env.py`: `--add-missing`, which appends only absent keys, leaves every existing line byte-identical and prints key names only.
- `app/db/connect.py`: `role_conninfo(role)` swaps user and password into `DATABASE_URL`. A missing password raises an error that names the key.
- `app/db/migrate.py` and `scripts/db_migrate.py`: apply `0002`, then create or alter the three roles and their grants idempotently. Nothing printed contains a password.
- `app/mcp_server/server.py`:
  - `GuardedTool` gains a per-subject rate limit and audit rows: one per call, or attempt plus result for the write tool.
  - `create_support_ticket` fails closed with `AUDIT_UNAVAILABLE`.
  - The `run_id` header is read and validated as a UUID.
  - `build_app` wraps the app in the body-limit, pre-auth rate-limit and pre-tool-refusal audit middleware.
  - (Step 5 finding) `SafeJsonRpcErrors` rewrites SDK JSON-RPC errors and "Unknown tool" results, because the SDK echoes submitted values (method name, tool name, validation input). `app/security/body_limit.py` drains a refused body (capped at 1 MiB), so Windows clients read the 413 instead of a connection reset.
- `app/mcp_server/tools.py`: the ticket store connects as `devguard_ticket`; audit uses a separate `devguard_audit` factory.
- `app/mcp_server/errors.py`: codes `RATE_LIMITED`, `AUDIT_UNAVAILABLE`, `PAYLOAD_TOO_LARGE`.
- `app/agent/tools.py`: sends the run ID as an `X-DevGuard-Run-Id` header.
- `app/agent/llm.py`: the OpenAI client gets an HTTP client with `follow_redirects=False`.
- `app/agent/harness.py`: optional extra planted documents for the injection cases; an optional audit factory.
- `app/api/app.py`: per-subject rate limits (agent-run routes costlier), the body-limit and pre-auth middleware, and the 429/413 envelopes.
- `scripts/api_server.py`, `scripts/mcp_server.py`: connect as their roles and refuse the owner fallback; run the egress check at start-up.
- `tests/unit/test_compose.py`: every port publishes on `127.0.0.1` (extended if not already complete), and the API and MCP refuse a non-loopback host.
- `tests/unit/test_init_env.py`, `tests/unit/test_db_migrate.py`, `tests/unit/test_mcp_tools.py`, `tests/unit/test_api.py`, `tests/integration/test_mcp_server.py`, `tests/integration/test_api_server.py`, `tests/integration/test_crash_resume.py`, `tests/integration/test_db_migrate_cli.py`: regression updates. They switch to the role DSNs. (Step 5, as built:) the in-process P4–P6 suites (`test_mcp_tools.py`, `test_api.py`, `test_api_threads.py`) pass a deliberately lenient limiter to `build_server`/`create_app`, because they test behaviour, not throttling. The subprocess burst tests use a dedicated server, because the per-address bucket is shared by every loopback caller. `test_ticket_tool.py` uses its own secret marker, so the crash/resume dump of the shared test schema stays order-independent.
- `app/db/connect.py` also honours `DEVGUARD_DB_DOTENV` (step 3), and `app/mcp_server/tools.py` gets `default_context(database=False)`, so tests and the harness never read the developer's `.env` database.
- `tests/integration/test_api_server.py`, `tests/unit/test_ticket_tool.py`, `tests/unit/test_agent_hitl.py`, `tests/unit/test_api_threads.py`: (step 3) DB-less contexts, or the throwaway audit role, because the write tool fails closed without an audit writer.
- `tests/conftest.py`: a `db_roles` fixture that creates random-named roles on the throwaway schema and drops them afterwards.
- `docs/threat-model.md`: T1–T6, T9–T12 and T14 gain P7 evidence; residual risks get the spec's flagged items.
- `docs/tech.md`, `docs/structure.md` (`app/security/` exists now), `README.md` (setup gains `init_env.py --add-missing`, then `db_migrate.py`), `docs/index.md`.
- `CLAUDE.md`: see Agent configuration changes.

Not changed: `app/validators/*`, `app/rag/*`, `app/db/store.py` queries, the `0001` migration, `.github/workflows/ci.yml` (the suite runs in the existing test job), `.github/workflows/agent-config-eval.yml`, `.github/workflows/security-scan.yml`, `.claude/settings.json`, `.claude/hooks/*`.

## Work sequence

1. **Config and `app/security/`.** Add the `security.yaml` keys; `ratelimit.py`, `body_limit.py`, `egress.py`. (spec AC #16; building blocks for #7–#9)
2. **Roles, audit table and env.**
   - Code: `0002` SQL, `roles.py`, `migrate.py`, `db_migrate.py`, `connect.role_conninfo`, `configs/agent.yaml` roles, `.env.example`, `init_env.py --add-missing`.
   - Tests: the conftest roles fixture.
   - (AC #11, #13, #14)
3. **Audit writer and MCP wiring.**
   - `audit.py`; `GuardedTool` audit, plus the fail-closed path for the write tool.
   - Pre-tool refusal audit middleware; the run ID header from `app/agent/tools.py`; `purge_audit.py`.
   - (AC #1–#6)
4. **Services connect as their roles; egress and redirects.**
   - `api_server.py` and `mcp_server.py` use role DSNs and refuse the owner fallback.
   - The egress check at start-up; `llm.py` with redirects off.
   - P6 regression tests on role DSNs.
   - (AC #12, #15, #16, #17)
5. **Rate and size limits on both servers; malformed input.** Edit `app/api/app.py` and `server.py`/`build_app`, and the regression subjects. (AC #7–#10)
6. **SSRF socket guard and loopback pins.** `test_injection.py` guard, `test_compose.py`. (AC #18, #23)
7. **Injection cases, CI mode.** Dataset, planted fixtures, harness option, `test_injection.py`. (AC #19)
8. **Security suite and evidence.**
   - `security_cases.yaml`, `test_security_suite.py` (including secret-in-prompt across tables, checkpoints and captured server logs).
   - `eval_security.py`, `test_eval_security_cli.py`.
   - (AC #21, #22)
9. **Container scan job.** `.github/workflows/container-scan.yml`, shown as a diff and written only after `approve write`. The `pip-audit` result is recorded from a GitHub Actions run of `security-scan.yml` on this branch; the dispatch needs your OK when we get there. (AC #24)
10. **Live injection run.**
    - **You:** start the pod and the tunnel (GPU spend).
    - **Me:** `uv run python scripts/eval_security.py --live-injection`.
    - **You:** terminate the pod.
    - First to cut if time runs short (spec Flagged concerns). (AC #20)
11. **Report and docs.** `security-report.md` rendered from the evidence, plus triage of the scan findings; `transport.md`; threat model; tech, structure, README and index. (AC #25)
12. **`CLAUDE.md` merge.** Shown as a diff, written only after `approve write`. (Agent configuration)

AC #26 (full suite and the `-m db` run, outputs pasted) is checked at every step and again in Phase 4.

Steps 6 and 7 touch the same new test file, so they run in order. Step 9 shares no file with steps 3–8 and could run in parallel, but it's small, so I'll run everything in one session.

## Test coverage

Matched rows from `test-scope-by-stage.md`:
- **backend service / script entry point:** integration tests at the real entry points, run as subprocesses;
- **roles/permissions:** authentication and authorization integration tests against the real servers, plus the database role matrix;
- **any database:** a regression test, because this feature adds a table and changes which role runs every existing query;
- **existing project:** regression tests on changed behaviour;
- **always:** the agent-config eval.

Not matched: UI/e2e (there is no screen).

Database tests are `@pytest.mark.db`. They skip in CI with a reason and run locally (P6 decision).

- **Step 1 (unit):**
  - Limiter: the burst is allowed and the next call is refused; refill is computed from the fake clock; key eviction is bounded; two keys are independent.
  - Body limit: 413 for an oversized declared length and for an oversized chunked stream; the response holds no body bytes; small bodies pass through unchanged.
  - Egress: refused for the metadata address, RFC 1918, link-local, IPv6 ULA/link-local, unknown names and `file:`/`gopher:`; accepted for the shipped config. IP strings are built from parts.
- **Step 2:**
  - **Database (`db`):** `0002` applies; a re-run applies nothing.
  - **Role matrix (`db`), one assertion per spec Behaviour §4 cell:** for example, `devguard_app` inserting into `sandbox_tickets` gets a permission error, and `devguard_audit` selecting from the audit table gets one too.
  - **Integration:** `db_migrate.py` as a subprocess applies roles idempotently and prints no password; `init_env.py --add-missing` as a subprocess keeps existing lines byte-identical, adds only absent keys and prints no value; without the flag it still refuses.
  - **Unit:** `role_conninfo` names the missing key and never the DSN.
- **Step 3:**
  - **Database (`db`), real MCP subprocess on role DSNs:** one audit row per call for each outcome, two for the write tool; pre-tool 401 rows; no secret-shaped argument in any row; `run_id` stored from the header and refused when not a UUID.
  - **Unit:** audit failure makes the write tool return `AUDIT_UNAVAILABLE` with the ticket count unchanged, while a read tool still answers.
  - **Integration:** `purge_audit.py` as a subprocess deletes only old rows.
- **Step 4:**
  - **Integration:** both servers with `DATABASE_URL` but no role password exit with the key named.
  - **Regression (`db`):** the P6 crash/resume, HITL and ticket-tool tests pass on role DSNs with exactly one ticket.
  - **Integration:** a local stub answering 302 to a disallowed address is not followed by the vLLM client, the MCP tool client or the health probes.
- **Step 5:**
  - **Integration (auth/authz), real subprocesses:**
    - API: 429 with the envelope and `Retry-After` after the run-route burst, while a second subject is unaffected; 413 for declared and chunked oversize.
    - MCP: `RATE_LIMITED` after the per-subject burst, and pre-auth 429.
    - Malformed JSON, non-object bodies, unknown methods and tools, and extra or wrong-typed arguments get safe errors and the handler counter stays at 0.
  - **Regression:** the existing P4–P6 suites pass below the limits.
- **Step 6 (integration):**
  - Socket guard: URL-, private-IP-, metadata- and path-shaped strings in every free-text tool argument and in a chat message cause no connection outside the allowlist.
  - Loopback pins (unit) in `test_compose.py`.
- **Step 7 (integration, CI mode via the harness):** all 10 cases. For each, tool calls equal the benign baseline, nothing outside the role is called, there are zero tickets and approval decisions, and verdicts come only from validators.
- **Step 8:**
  - **Integration and database:** every §22.5 bullet has at least one case.
  - **Secret in prompt (`db`):** no synthetic secret appears in any table, checkpoint, captured server log or response.
  - **Integration:** `eval_security.py` as a subprocess writes the JSONL and the rendered report, and exits non-zero when a case fails.
- **Step 9:** the agent-config eval is unaffected (no `.claude/` change). The new workflow is checked by its first run on the PR.
- **Step 10:** a live run (evidence, not a unit test). The report records the measured result per case.
- **Step 11:** the existing `test_repo_hygiene.py` passes on the new docs (no IPv4-shaped strings outside `section_id`).
- **Step 12:** the agent-config eval: `.claude/hooks/eval.sh` re-run, output pasted.

## Agent configuration changes

- **`CLAUDE.md`** (step 12, diff plus `approve write`). Appends only:
  - **`## Commands`:**
    - `uv run python scripts/init_env.py --add-missing`
    - `uv run python scripts/purge_audit.py`
    - `uv run python scripts/eval_security.py [--live-injection]`
    - a note that `db_migrate.py` now also creates the roles
  - **Project status:** lists `app/security/`.
  - **`## Common mistakes`:** append-only, with lessons found during the build. Two candidates:
    - tests must never `ALTER ROLE` the real role names;
    - per-subject limits make high-volume test modules use distinct subjects.

  `## Test scope` already has every matched row (scripts, regression, PostgreSQL, roles/permissions), so it is unchanged.
- **Subagents:** none. `.claude/agents/` is empty and no recurring P7 task needs one.
- **Hooks:** none new. The existing `deploy-gate.sh` and `eval.sh` pairs are re-verified in Phase 4.
- **CI:**
  - `.github/workflows/container-scan.yml` (new file, new job, diff plus `approve write`);
  - no edit to `ci.yml`, `agent-config-eval.yml` or `security-scan.yml`.

## Rollback

- **Code:** revert the branch's commits.
- **Database:** drop the three roles, drop `tool_audit_logs` and delete its `schema_migrations` row (`0002`). The `0001` tables are untouched.
- **`.env`:** the three appended password lines can stay, because nothing reads them after a revert.
