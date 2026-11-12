# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

QRIS DevGuard AI is a capstone project. `docs/QRIS_DevGuard_AI_PRD_TRD.md` is the source of truth for requirements, architecture, data model, API contract and release gates. Read the relevant section before you implement anything.

Phase P0 (§27, scope and source inventory) is signed off (2026-11-06). The decision chain is in `docs/scope-and-source-inventory/`.
- `docs/scope.md` covers the use case, boundaries, roles and data owners.
- `docs/threat-model.md` lists threats T1–T20, each mapped to the phase that must prove its control.
- `skills/qris-integration-troubleshooting/SKILL.md` is the SOP outline.
- `knowledge_base/manifests/sources.yaml` is the source inventory, validated against `source.schema.json`.
- `configs/security.yaml` defines the role/tool matrix, redaction fields and egress allowlist.
- `configs/eval_thresholds.yaml` defines the release gates.

Application code so far: `app/rag/` (P2–P3), `app/validators/` and `app/mcp_server/` (P4), `app/agent/` and `app/api/` (P5), `app/db/` (P6: PostgreSQL checkpointer, approvals, sandbox tickets; P7: least-privilege roles, MCP audit), and `app/security/` (P7: rate limits, body-size limit, egress check). P8 adds `app/memory/` (policy-gated
preferences and reviewed team patterns, migration `0003`), `app/observability/` (Langfuse tracing with an
attribute allowlist and a fail-closed mask hook; deterministic scores), `app/agent/fence.py` and `app/db/retention.py`.
P9 adds `app/ui/` (Streamlit demo UI, calls `/api/v1` only) and `app/alerts/` (stdlib Langfuse alert webhook receiver, run as the `alert-receiver` compose service, and the API-side reader).

## Commands

```bash
uv sync                                                  # install deps into .venv
uv run pytest                                            # all tests
uv run pytest tests/unit/test_check_sources.py::test_duplicate_document_id_rejected   # single test
uv run python scripts/check_sources.py                   # validate source manifest (P0 exit check)
uv run python scripts/init_env.py                        # create .env with random secrets (refuses to overwrite)
podman compose up -d                                     # local PostgreSQL, Qdrant, Langfuse
bash scripts/tunnel.sh                                   # SSH tunnel to the RunPod vLLM on 127.0.0.1:8080
uv run python scripts/healthcheck.py                     # 7 PASS/FAIL checks; appends to evals/reports/healthcheck.jsonl
uv sync --extra embed                                    # once: CPU embedding stack (torch, sentence-transformers)
uv run python scripts/fetch_models.py                    # once: pinned BGE-M3 into the HF cache (truststore TLS)
uv run python scripts/ingest.py                          # approved sources -> Qdrant; unchanged checksum+config = skipped
uv run python scripts/search.py "<query>"                # hybrid search; results are untrusted data
uv run python scripts/search.py "<query>" --rerank       # hybrid top-20 reordered by the cross-encoder; fused and reranked ranks
uv run python scripts/eval_retrieval.py                  # Recall@5/10/20, MRR@10 on evals/datasets/retrieval_golden.yaml, plus the P3 before/after rerank table (original/hard/all)
uv run python scripts/gen_test_keys.py                   # once: test-only RSA/HMAC keys into the git-ignored knowledge_base/fixtures/signatures/keys/
uv run python scripts/extract_response_codes.py          # once: SNAP response-code table from the local PDF into git-ignored knowledge_base/sources/derived/
uv run python scripts/eval_validators.py                 # every validator fixture; exit 0 only at 100% (validator_fixture_accuracy)
uv run python scripts/mcp_server.py                      # MCP server, http://127.0.0.1:8765/mcp, bearer auth (MCP_AUTH_SECRET)
uv run python scripts/mint_dev_token.py --sub dev-1 --role developer   # short-lived bearer token; treat it as a secret
uv run python scripts/mint_dev_token.py --sub dev-1 --role developer --audience api   # token for /api/v1 (never accepted by MCP)
uv run python scripts/api_server.py                      # /api/v1 on 127.0.0.1:8000; needs the MCP server and the vLLM tunnel
uv run python scripts/eval_agent.py                      # scripted agent flows, CI mode (MCP harness + fake LLM, no services)
uv run python scripts/eval_agent.py --live --api-smoke   # the same flows on the real MCP server, Qdrant and vLLM (pod + tunnel)
podman compose up -d postgres && uv run python scripts/db_migrate.py   # P6 schema + LangGraph checkpoint tables; P7 roles, grants, audit table (re-runnable)
uv run pytest -m db                                      # PostgreSQL tests (throwaway schema); skipped with a reason without Postgres
uv run python scripts/init_env.py --add-missing          # after pulling P7: append missing keys (role passwords) to .env; prints names only
uv run python scripts/purge_audit.py                     # MCP audit rows older than audit.retention_days (90), as the owner role
uv run python scripts/purge_retention.py --dry-run       # P8: what retention would delete (checkpoints 30 d; tickets/approvals/threads 90 d; deleted or expired memory), counts only
uv run python scripts/purge_retention.py                 # the same deletes, as the owner role, in one transaction
uv run python scripts/scan_markers.py                    # P8: sentences answer-echo fencing would remove, per retrievable document (IDs and counts only)
uv run python scripts/eval_security.py                   # §22.5 suite + §13.4 checklist -> docs/p7-mcp-hardening/security-report.md
uv run python scripts/eval_security.py --live-injection  # the 10 injection cases on the real vLLM (pod + tunnel)
uv sync --extra ui                                       # once: Streamlit for the demo UI
uv run python scripts/ui_server.py                       # demo UI on http://127.0.0.1:8501; needs the API (demo-only: mints tokens from MCP_AUTH_SECRET)
podman compose --profile alerts up -d alert-receiver     # Langfuse alert webhook receiver, compose network only (http://alert-receiver/langfuse/alerts)
uv run python scripts/send_test_alert.py --via-container qris-devguard-agent_langfuse-worker_1   # one signed synthetic alert, from where Langfuse sends
```

Source manifest rules (enforced by `scripts/check_sources.py`):
- Only `approved` entries may be ingested. An approved entry needs a version, an effective date (`YYYY-MM` or `YYYY-MM-DD`) and confirmed permission with evidence.
- `reference_only` (public ASPI/SNAP documents with no published terms) needs a real URL. Its files stay under the git-ignored `knowledge_base/sources/` and are never committed. Citations quote at most 300 characters (`configs/security.yaml`).
- `team_authored` documents have the ID prefix `synthetic-` and the title marker `(SYNTHETIC — not an official ASPI/BI document)`, and are committed under `knowledge_base/synthetic/`.
- Never guess versions, dates or URLs; leave them `null` until confirmed.

## What the system is

A self-hosted agent that diagnoses QRIS/SNAP API integration problems: signatures, payloads, timestamps and response codes. It combines hybrid RAG with a LangGraph agent, and runs vLLM on a RunPod GPU.

Planned stack: Python 3.11+, FastAPI/Pydantic, LangGraph with a PostgreSQL checkpointer, vLLM (OpenAI-compatible, Qwen 7–8B class), BGE-M3-class embeddings, a BGE reranker v2 M3 class reranker, Qdrant (dense + BM25 fused with RRF), PostgreSQL, a Python MCP SDK server and Langfuse.

The planned layout is in TRD §24 (`app/{api,agent,rag,mcp_server,validators,memory,observability,security,db}`, `configs/*.yaml`, `skills/qris-integration-troubleshooting/`, `knowledge_base/`, `evals/`, `scripts/`).

## Request flow (TRD §10–11)

The LangGraph nodes run in this order:

`sanitize_input → classify_intent → classify_risk → retrieve_evidence → rerank_evidence → validate_evidence → select_tools → execute_read_tools → compose_diagnosis → policy_check → [request_human_approval → execute_approved_action] → memory_decision → finalize`

The state schema is `DevGuardState` (§11.1).

Routing rules:
- A validation statement requires a call to a deterministic tool first.
- Any write action goes through `request_human_approval`, an interrupt with a persisted checkpoint.
- Missing or ambiguous evidence leads to an abstention or a clarification question.
- High-risk or malformed requests never reach an execution tool.

MCP tools (§13.2):
- Read-only: `search_qris_spec`, `validate_request_payload`, `validate_test_signature`, `lookup_response_code`
- Write, approval required: `create_support_ticket`

## Invariants that must hold in all code

These are automatic-FAIL conditions in the release gate (§22.6):

- **The LLM never decides crypto or payload validity.** Deterministic validators decide it:
  - Payloads are checked against versioned JSON Schema, with business rules kept as separate checks.
  - Signatures use named, versioned canonicalization functions against test-only RSA/HMAC fixtures.
  - The LLM only explains validator output.
  - Validators return `INCONCLUSIVE` or `NOT_FOUND` instead of guessing. Unknown response codes are never given an invented meaning.
- **No secrets anywhere.** Redact `Authorization`, `access_token`, `client_secret`, `private_key`, `secret`, `password` and `signature` fields *before* DB persistence, checkpoint serialization and Langfuse tracing. Graph state holds only sanitized values, fixture IDs or hashes. Test key material lives outside version control. Only synthetic data is allowed.
- **Side effects are approval-gated and idempotent.**
  - Each approval is bound to the exact action payload, user and thread, and expires. Changing the payload invalidates it.
  - `idempotency_key = hash(thread_id + action_type + normalized_action_payload)`, with a unique DB constraint. Do not rely on thread-ID replay alone.
  - The only write target is a local sandbox ticket table. Payment, refund and production operations must not exist as tools.
- **Citations must resolve.** Every material doc claim cites a chunk retrieved *in that run* (§12.4 schema). When versions conflict, show both sources and do not merge them silently (§12.5).
- **Treat retrieved text and user input as untrusted.** They can never expand tool authority. MCP tools take no arbitrary URL, SQL, shell or path arguments, and every tool validates its arguments with strict Pydantic models.
- **Long-term memory defaults to not saving** (§16). Never store credentials, personal or merchant data, or unverified inferences. Memory is kept separate from checkpoint state.

## Conventions from the spec

- All API routes go under `/api/v1`, plus `/health/live` and `/health/ready`. The error envelope is `code`, `message`, `correlation_id` and optional `field_errors`, with no stack traces (§18).
- Final answers follow the response contract in §5: Summary, Findings (confirmed/likely/unverified), Validation results, Next steps, Sources, Confidence/limitations.
- Keep the model ID, quantization, context length, retrieval parameters and eval thresholds in `configs/*.yaml`, not in code.
- The values in PRD §6 and §22 are targets. Report measured results and never present targets as results.

## Project context
Read `docs/product.md`, `docs/tech.md` and `docs/structure.md` before
making changes — they describe what this project is, what it's built
with, and how it's laid out. Keep them current when the stack or
layout changes.

## Documentation layout
All documentation lives under `docs/`, and `docs/index.md` is the table of contents. Each feature gets its own folder, `docs/<feature-name>/`. Update `docs/index.md` in the same change whenever a feature is added or changed.

## Test scope
- Scripts and CLI entry points: add an integration test that runs the real entry point as a subprocess (`tests/integration/`), in addition to unit tests.
- Changes to existing behaviour: add a regression test, and keep the existing tests passing unchanged.
- Database (PostgreSQL): add a regression test whenever a step changes the schema or an existing query or behaviour around stored data.
- Qdrant collection config or payload fields: add a regression test when either changes (`tests/unit/test_rag_store_ingest.py` pins them).
- Roles/permissions (MCP tools, later the API): add authentication and authorization integration tests against the real server subprocess (`tests/integration/test_mcp_server.py`), plus the role × tool matrix unit test.
- PostgreSQL tests: mark them `@pytest.mark.db` and use the throwaway-schema fixtures in `tests/conftest.py`. CI has no PostgreSQL, so they skip there; paste the local `uv run pytest -m db` run in the PR.
- UI (Streamlit): AppTest end-to-end against the real API and MCP subprocesses (`tests/integration/test_ui_e2e.py`), plus one manual browser pass per release (`docs/p9-demo-ui-alerts/runbook.md`). UI tests use `pytest.importorskip("streamlit")`; the `ui-tests` CI job installs the `ui` extra.

Run `uv run pytest` before reporting a task complete, and paste the output.

## Workflow
Nothing is implemented without an accepted plan. Each feature follows `intent.md` → `spec.md` → `plan.md` (+ `tasks.md`) in `docs/<feature-name>/`, each committed before the next starts.

## Common mistakes
- Piping `uv run pytest | tail` hides pytest's exit code, so a red suite can be committed. Gate on pytest itself, or use `set -o pipefail`.
- `tests/unit/test_repo_hygiene.py` flags any IPv4-shaped string in tracked files, including four-part section numbers written in docs. Only a quoted `section_id: "…"` value is exempt.
- On CPU, `scripts/eval_retrieval.py` with reranking takes hours on the full golden set (224 min measured in P3: about 160 s per query). Use `--no-rerank` for P2-style metrics, and run the reranked eval in the background.
- Registering an MCP tool with the SDK's plain `@server.tool` lets argument errors reach the client as pydantic text, which includes the submitted values. Register every tool through `GuardedTool` (`app/mcp_server/server.py`) so errors use the §18 envelope.
- `approved` is not the same as "retrievable". The fixture index (`kind: fixture_set`) is approved but never ingested; use `manifest.retrievable_document_ids()` when you need the documents a query can cite.
- A committed file whose checksum is in the manifest needs `text eol=lf` in `.gitattributes`, or Windows checkouts change its bytes.
- SSRF and IP test strings trip the IPv4 check in `test_repo_hygiene.py`. Build them from parts (`".".join([...])`) rather than writing the literal.
- The hygiene tests read *tracked* files only, so a new file with a PEM header or an IP passes them until it is committed. Stage new files (`git add`) before running the gate. Build PEM headers in tests from parts too.
- On this Windows machine a refused loopback port can hang (probably the Podman/WSL relay) instead of refusing, while Linux CI refuses instantly. Any "service down" test or readiness probe needs a short client timeout.
- `redact_fields` in `configs/security.yaml` includes `signature`, which is also the name of the API's `signature` context block. Redaction applies inside context blocks, never to the block names.
- LangGraph checkpoints the graph's raw input before any node runs. Sanitize before `graph.ainvoke`, not only in `sanitize_input`. A secrets test that reads only the final state misses this; walk the whole checkpoint history or dump the tables.
- A node that calls `interrupt()` re-runs from its first line on resume. Create side-effect records (like the approval) in an earlier node, keep them idempotent, and read the decision from the database, not the resume value.
- uvicorn's `asyncio` loop is the Proactor loop on Windows, which psycopg's async driver cannot use. The API passes `loop="asyncio:SelectorEventLoop"`, and `tests/conftest.py` sets the selector policy.
- Text like `validate_test_signature: INVALID` reads as a `signature: <value>` secret pair to the redaction rules, and the ticket tool refuses it. Write `tool -> result` in generated ticket text.
- Tests must never `CREATE`/`ALTER ROLE` the real `devguard_app`, `devguard_ticket` or `devguard_audit`: that would change the developer's passwords. Use the `db_roles` fixture (random throwaway names, dropped afterwards).
- The pre-auth rate limit keys on client address, and every caller here is `127.0.0.1`, so one test's burst throttles every other test on that server. Burst tests get a dedicated server; behaviour suites pass a lenient limiter or `McpHarness(rate_limits=False)`.
- uvicorn trusts `X-Forwarded-For` from loopback by default, which lets any local client dodge the per-address limit. Run every server with `proxy_headers=False` until a real proxy exists (`docs/p7-mcp-hardening/transport.md`).
- A `--live` script must load `VLLM_*` from `.env` (`api_server._load_env_file`) before pinging vLLM, and a live-mode socket guard must allow the configured `VLLM_BASE_URL` port. CI mode (fake LLM) catches neither.
- `scripts/eval_security.py` grades a skipped test NOT RUN, never PASS. Start Postgres first, or every database case reads NOT RUN.
- `gh workflow run` only works for a workflow that already exists on `main`. A new workflow first runs through its `pull_request` trigger.
- A Langfuse container can't reach a listener on the host's `127.0.0.1`, and Langfuse delivers webhooks only to ports 80/443 and refuses private hosts unless whitelisted. Webhook receivers go in the compose network (`alert-receiver`, whitelisted by hostname only).
- Streamlit re-runs the whole script on every click, so an uncached API read per rerun empties the subject's `api_other` bucket (429) in a few clicks. Read through `read()` (per-session cache) and write through `write()` (clears it) in `app/ui/streamlit_app.py`.
- Streamlit widget labels and `st.markdown`/`st.info`-style elements render markdown. Show API text (answers, excerpts, approval payloads, alerts) with `st.text` only.
- On Windows `Popen.terminate()` kills only the launcher, not a child it spawned, and the child keeps the pipes open, so `communicate()` hangs. Launchers run their server in-process (`scripts/ui_server.py` calls `streamlit.web.cli.main`).
- In LangGraph's checkpoint history, the next run's input checkpoint still carries the previous run's values with nodes pending. Take a run's answer from its newest checkpoint with no `next` (`Agent.history`).
- Another session's `git commit` takes everything staged, including your files. Stage only at commit time: `git commit --only -- <paths>`.
- `docker-compose.yml` reads `LANGFUSE_ALERT_WEBHOOK_SECRET` with `:?`, so every `podman compose` command fails until `.env` has it: run `init_env.py --add-missing` after pulling P9.
- Chaining `uv run pytest ...; git commit` commits on red just like piping does. Commit only inside an `if` on pytest's own exit code.
- OpenTelemetry records an exception escaping a span as an event carrying its message and stack trace, and `mask_otel_spans` can't change events. Open spans only through the helpers in `app/observability/tracing.py`, which record the class name and re-raise.
- Langfuse's resource manager is a singleton per public key, and its default set-up replaces the global OpenTelemetry provider. Tests build each tracer with a unique public key and an in-memory exporter (`tracing.build(env, span_exporter=...)`, which also gives it its own `TracerProvider`).
- The servers load `LANGFUSE_*` from `.env`, so a server started by a test would export to the local Langfuse. `tests/conftest.py` sets `DEVGUARD_TRACING=0`; tracing tests pass explicit env mappings.
- Corpus excerpts are hard-wrapped. Split sentences at end punctuation or a blank line, never at a single newline, or a wrapped attack sentence slips past `injection_markers`.
- A failing `db` test prints its fixture URL, which contains the local Postgres password. Filter out `password=` before pasting failing test output anywhere.
