# Intent: Every TRD §13.4 go-live control has recorded evidence, and the §22.5 MCP security test set shows zero unauthorized side effects and no secret leakage
*Author: Kharisma Wardhana. Stage: Plan. Written: 2026-11-11. Via /aspec:make-it-simple-v2.*

## Problem statement

P4–P6 built the controls piecemeal. Each phase proved the slice it touched, but nobody has checked the whole set against the go-live checklist. The PRD's P7 exit criterion, *P7 checklist evidence complete*, isn't met:

- **No single evidence record.** The 16 controls in TRD §13.4 have no evidence table. TRD §22.5 also asks for a test report listing each payload, the expected result, the actual result and the evidence. Neither exists. Proof that does exist is scattered across test files and the threat-model "Verified by" column.
- **Controls that are missing entirely:**
  - **Auditability.** The MCP server writes one log line per call. Nothing durable records actor, tool, timestamp, outcome and correlation ID. The `tool_audit_logs` table was deferred from P6.
  - **Rate limiting (T12).** Neither the API nor the MCP server throttles. A burst of calls reaches the agent, Qdrant and the GPU pod unchecked.
  - **Least privilege (§13.3).** The API, the agent and the ticket tool all connect as the `devguard` owner role. This was deferred from P6 (flagged in the P6 spec).
  - **SSRF (T9).** There is no URL-taking tool, but no test proves that localhost, private, link-local, metadata or redirect targets are refused wherever a string could carry one.
  - **Prompt injection (T3).** The 10 malicious user and document cases from §22.5 haven't been written. P5 proved only that one injected "call create_support_ticket" changes no tool call.
  - **Transport.** Every listener binds `127.0.0.1` and vLLM is reached over an SSH tunnel. No test pins this, and no note says how TLS would apply to a future public ingress.
  - **Dependency and container hygiene.** `security-scan.yml` runs `pip-audit` weekly and non-blocking. Its findings haven't been recorded or triaged, and no container image has been scanned.
- **Controls that are partly proven:**
  - authentication and authorization (T1, T2)
  - input validation and argument injection (T10)
  - error handling (T14)
  - approval bypass (T4)
  - duplicate replay (T5)
  - state isolation (T11)
  - secret redaction (T6)

  They need the remaining §22.5 cases. Malformed JSON, oversized input, and an attacker who probes from the API side as well as the MCP side are still untested.

## Proposed outcome

- **A §22.5 security test suite** that runs in CI, covering every bullet in TRD §22.5. Each case lists its payload, expected result and actual result, plus evidence that no side effect occurred: the ticket count is unchanged and nothing leaked.
  - Cases that need PostgreSQL (approval replay, cross-thread substitution, duplicate key) run locally under `-m db`, like P6.
- **A durable MCP audit trail.** Every tool call, whether allowed or denied, records actor, role, tool, timestamp, outcome and correlation ID. It never records arguments, secrets or whole payloads (TRD §19.1). Audit rows are kept for 90 days (TRD §19.3).
- **Rate limiting** on the API and the MCP server, with limits set in `configs/`. A burst test shows the excess calls throttled with a safe structured error.
- **Least-privilege database access.** The API/agent and the MCP ticket tool each get a role that can touch only the tables and actions they need. A test proves a role can't reach the other's tables.
- **SSRF and transport tests:**
  - SSRF: no tool, client or argument can be steered to localhost, private, link-local, metadata or redirect targets, and outbound hosts stay inside `egress_allowlist`.
  - Transport: every service binds loopback only. Using TLS for a future public ingress is documented, not run.
- **10 prompt-injection cases.** They run in CI mode (harness + fake LLM) as the gate, and once live against vLLM in one short pod session. That proves retrieved or user text never expands tool authority.
- **Dependency hygiene evidence.** A recorded `pip-audit` run with every finding remediated or documented with a mitigation, and the status of the container images used.
- **The go-live evidence table** `docs/p7-mcp-hardening/security-report.md`. It maps each of the 16 §13.4 controls to its evidence and a PASS or FAIL, and lists the §22.5 test matrix. Measured results only.

## Affected users and systems

- Kharisma Wardhana: runs the local `-m db` suite and the one live pod session, and signs off the evidence table.
- `app/mcp_server/`: audit writes, rate limiting, ticket-tool database role.
- `app/api/`: rate limiting; the database role for the API and agent.
- `app/db/`: migration for the audit table and the per-tool roles.
- `configs/`: rate limits and the audit retention period. `security.yaml` changes only if a control needs it, with the threat model reviewed alongside (its header rule).
- `scripts/init_env.py` / `.env.example`: credentials for the new database roles, without overwriting an existing `.env`.
- `tests/`: the §22.5 security suite (unit plus integration against the real server subprocesses).
- `docs/threat-model.md`: T1–T6, T9–T12 and T14 gain P7 evidence.
- CI: the security suite runs in the existing test job. Database cases skip there with a reason, as in P6.
- Downstream: P8 (Langfuse traces and the mandatory alert use the audit and correlation IDs), P9 (release gate "MCP security").

## Constraints

- **Deadline:** P7 is done by 2026-11-13. All of P1–P9 must be done by 2026-11-20.
- **Checklist:** TRD §13.4 and §22.5 are the exit criteria. There is no separate course checklist (user decision).
- **Loopback only:** nothing becomes externally reachable. TLS is documented for a future ingress, not deployed (user decision).
- **GPU:** at most one short pod session, for the live injection run. The pod is terminated after it.
- **Zero unauthorized side effects** and no secret leakage. These are automatic FAIL conditions (TRD §22.6). The audit trail itself must not become a leak.
- **Only synthetic data.** Attack payloads are synthetic too. SSRF and IP test strings are built from parts, because of the repo-hygiene IPv4 check (CLAUDE.md).
- **No new write targets.** The only side effect is still the local `sandbox_tickets` table.
- **Existing `.env` files are never overwritten.** New role credentials must reach an existing setup some other way.
- **Must not break:** P2–P6 behaviour and all existing tests. That includes P6's crash/resume and exactly-once guarantees.
- **Targets aren't results:** the report states measured outcomes only.

## Open questions

- Where does the audit trail live? A `tool_audit_logs` table written by the MCP server, an append-only file, or both? Does the API also audit its own routes (approval decisions especially), or only MCP tool calls?
- How does the MCP server write audit rows without becoming a second path to the database the ticket role can't use? Does it get its own audit-only role?
- Rate limiting: per subject, per role or per IP? Fixed window or token bucket? In-process memory (single process, loopback) or PostgreSQL-backed? What limits, and which error code (HTTP 429 with the §18 envelope)?
- Per-tool database roles: which roles exactly, created by whom (a migration run as the owner?), and how do their passwords reach an existing `.env` that `init_env.py` refuses to overwrite? Does the LangGraph checkpointer need DDL rights at runtime, or only after `db_migrate.py`?
- What counts as SSRF evidence when no tool takes a URL? A test that every model-reachable string field rejects URL and IP shapes, plus a check that the configured service URLs (vLLM, Qdrant, Postgres, Langfuse) fall inside `egress_allowlist`?
- Which 10 injection cases? How many are user-turn and how many document-borne? How is a document-borne case planted without changing the approved corpus (the harness's in-memory corpus, and for the live run an un-ingested synthetic chunk)?
- "Container scan": the stack uses upstream images (Postgres, Qdrant, Langfuse, vLLM) and builds none of its own. Is a pinned-tag and digest record plus a local Trivy/Grype scan enough, or is this documented as not applicable?
- Do any current `pip-audit` findings block, and what is the rule for "documented with mitigations" rather than remediated?
- Malformed JSON at the MCP transport layer: does the SDK reject it before the `GuardedTool` envelope, and is that response safe (no echoed values)?
