# QRIS DevGuard AI

A self-hosted agentic assistant for diagnosing **sandbox** QRIS/SNAP API integration issues. It covers signatures, timestamps, payloads and response codes, and its answers are backed by cited documentation and deterministic validators. It is a capstone project: synthetic data only, with no production connectivity.

- Requirements: [`docs/QRIS_DevGuard_AI_PRD_TRD.md`](docs/QRIS_DevGuard_AI_PRD_TRD.md)
- Phase P0 scope and boundaries: [`docs/scope.md`](docs/scope.md)
- Threat model: [`docs/threat-model.md`](docs/threat-model.md)
- SOP skill: [`skills/qris-integration-troubleshooting/SKILL.md`](skills/qris-integration-troubleshooting/SKILL.md)
- Source inventory: [`knowledge_base/manifests/sources.yaml`](knowledge_base/manifests/sources.yaml)
- Running the stack (local services + vLLM on RunPod): [`docs/runpod-deployment.md`](docs/runpod-deployment.md)

## Status

Phase P0 (scope and source inventory) is signed off. Phase P1 (infrastructure) is complete: 7/7 health checks passed on 2026-11-07 (`docs/p1-infrastructure/`). Phases P2 (knowledge pipeline) and P3 (reranking and eval) are complete; see their folders under `docs/`. Phase P4 (deterministic MCP tools) is complete. Phase P5 (LangGraph agent) is complete. Phase P6 (checkpoint and human approval) is in review: `docs/p6-checkpoint-hitl/`. Crash/resume passes locally with exactly one ticket. Live flows pass 3/4; flow 1 is still a recorded fail. Phase P7 (MCP hardening) is in progress: `docs/p7-mcp-hardening/`, with the measured §13.4 checklist in its security report.

## Development

```bash
uv sync                                   # install deps (.venv)
uv run pytest                             # all tests
uv run python scripts/check_sources.py    # validate the source manifest
uv run python scripts/init_env.py         # once: create .env with random secrets
uv run python scripts/init_env.py --add-missing   # after pulling a phase that adds keys (P7: role passwords)
podman compose up -d                      # local PostgreSQL, Qdrant, Langfuse
uv run python scripts/healthcheck.py      # PASS/FAIL per service
```

## Knowledge pipeline (P2)

```bash
uv sync --extra embed                                  # once: CPU embedding stack (torch, sentence-transformers)
uv run python scripts/fetch_models.py                  # once: pinned BGE-M3 and reranker weights (~2.3 GB each) into the HF cache
uv run python scripts/ingest.py                        # approved sources -> Qdrant (re-runs skip unchanged files)
uv run python scripts/search.py "X-SIGNATURE tidak valid"
uv run python scripts/search.py "X-SIGNATURE tidak valid" --rerank   # fused vs reranked ranks
uv run python scripts/eval_retrieval.py                # Recall@5/10/20, MRR@10: dense, sparse, hybrid; before/after reranking
```

The two ASPI PDFs are reference-only: put them at the `local_path`s in `knowledge_base/manifests/sources.yaml`. They are git-ignored and never committed.

## MCP tools (P4)

```bash
uv run python scripts/gen_test_keys.py                 # once: test-only keys, git-ignored
uv run python scripts/extract_response_codes.py        # once: SNAP response-code table from the local PDF, git-ignored
uv run python scripts/eval_validators.py               # all validator fixtures; must be 100%
uv run python scripts/mcp_server.py                    # http://127.0.0.1:8765/mcp, loopback only
uv run python scripts/mint_dev_token.py --sub dev-1 --role developer   # bearer token for a client
```

Four read-only tools: `search_qris_spec`, `validate_request_payload`, `validate_test_signature`, `lookup_response_code`.

- **MCP SDK:** `mcp==2.3.0` (Python). **Protocol:** up to `2026-07-28`; the initialize handshake supports `2024-11-05` to `2025-11-25`, and the SDK's `ClientSession` negotiates `2025-11-25` (TRD §13.1).
- **Transport:** Streamable HTTP, stateless, JSON responses, bound to `127.0.0.1`, DNS-rebinding protection, 64 KiB body limit.
- **Auth:** HS256 bearer token signed with `MCP_AUTH_SECRET`, carrying `sub` and `role`, at most 60 minutes. Roles per tool come from `configs/security.yaml`.

## Agent and API (P5)

```bash
uv run python scripts/mcp_server.py                                   # tools (terminal 1)
bash scripts/tunnel.sh                                                # vLLM on the RunPod pod (terminal 2)
uv run python scripts/api_server.py                                   # http://127.0.0.1:8000 (terminal 3)
uv run python scripts/mint_dev_token.py --sub dev-1 --role developer --audience api
uv run python scripts/eval_agent.py                                   # scripted flows, CI mode (no services)
uv run python scripts/eval_agent.py --live --api-smoke                # scripted flows against the real stack
```

## Threads and approvals (P6)

```bash
podman compose up -d postgres
uv run python scripts/db_migrate.py                                   # once
uv run python scripts/api_server.py                                   # prints "PostgreSQL checkpointer, approvals on"
```

`POST /api/v1/threads`, `POST /api/v1/threads/{id}/messages` and `GET /api/v1/threads/{id}` are owner-only. A request to create a ticket is diagnosed first, then pauses at `awaiting_approval`. A different user with the `support_engineer` or `admin` role decides with `POST /api/v1/approvals/{id}/decision`, and the approval expires after 30 minutes. `POST /api/v1/threads/{id}/resume` finishes a run after a crash. The only side effect is one row in the local `sandbox_tickets` table, at most once.

`POST /api/v1/chat` takes `{message, context}`. The context can carry `api_family`, `spec_version`, `endpoint_id`, a sanitized `payload`, a `signature` test case or a `response_code`. The reply is a PRD §5 answer: summary, findings, validation results, next steps, sources, confidence and limitations.

## Hardening (P7)

```bash
uv run python scripts/init_env.py --add-missing                      # once after pulling P7: the three role passwords
uv run python scripts/db_migrate.py                                   # creates the roles and the audit table
uv run python scripts/eval_security.py                                # §22.5 suite + §13.4 checklist -> security-report.md
uv run python scripts/purge_audit.py                                  # audit rows older than 90 days
```

The API and the MCP server now connect to PostgreSQL as least-privilege roles, and refuse to start without their role passwords rather than fall back to the owner. Every MCP tool call is audited. Both servers rate-limit (per address before auth, per subject after) and refuse oversized bodies, and outbound destinations are checked at start-up. Evidence and the measured go-live checklist are in [`docs/p7-mcp-hardening/security-report.md`](docs/p7-mcp-hardening/security-report.md).

## Memory, tracing and retention (P8)

```bash
uv run python scripts/db_migrate.py                                   # adds the memory tables (migration 0003)
uv run python scripts/purge_retention.py --dry-run                    # what retention would delete, counts only
uv run python scripts/purge_retention.py                              # delete it (owner role)
uv run python scripts/scan_markers.py                                 # fencing false-positive count over the corpus
```

Saving memory is opt-in. A user saves a preference (`language`: `id`/`en`, `detail`: `brief`/`detailed`) with `PUT /api/v1/memory/preferences/{key}` or a fixed chat phrase such as `remember: answer in Indonesian`. Team troubleshooting patterns are proposed with `POST /api/v1/memory/patterns` and are shown to the model only after a different support engineer or admin approves them, as untrusted notes that are never cited.

With real `LANGFUSE_*` keys in `.env` and the local Langfuse running, every run is one trace with node, LLM, MCP-call and retrieval spans, plus deterministic scores (`citation_valid`, `answer_supported`, `tool_success`, `policy_pass`, `answer_echo`, `incident_event`, and `user_feedback` from `POST /api/v1/threads/{id}/feedback`). Spans carry IDs, codes and timings only, never message, answer or document text. The alert setup and its controlled test are in [`docs/p8-memory-observability/alerts.md`](docs/p8-memory-observability/alerts.md), and measured results are in [`observability-report.md`](docs/p8-memory-observability/observability-report.md).

