# QRIS DevGuard AI

A self-hosted agentic assistant for diagnosing **sandbox** QRIS/SNAP API integration issues. It covers signatures, timestamps, payloads and response codes, and its answers are backed by cited documentation and deterministic validators. It is a capstone project: synthetic data only, with no production connectivity.

- Requirements: [`docs/QRIS_DevGuard_AI_PRD_TRD.md`](docs/QRIS_DevGuard_AI_PRD_TRD.md)
- Phase P0 scope and boundaries: [`docs/scope.md`](docs/scope.md)
- Threat model: [`docs/threat-model.md`](docs/threat-model.md)
- SOP skill: [`skills/qris-integration-troubleshooting/SKILL.md`](skills/qris-integration-troubleshooting/SKILL.md)
- Source inventory: [`knowledge_base/manifests/sources.yaml`](knowledge_base/manifests/sources.yaml)
- Running the stack (local services + vLLM on RunPod): [`docs/runpod-deployment.md`](docs/runpod-deployment.md)

## Status

Phase P0 (scope and source inventory) is signed off. Phase P1 (infrastructure) is complete: 7/7 health checks passed on 2026-11-07 (`docs/p1-infrastructure/`). Phases P2 (knowledge pipeline) and P3 (reranking and eval) are complete; see their folders under `docs/`. Phase P4 (deterministic MCP tools) is in review: `docs/p4-deterministic-mcp-tools/`.

## Development

```bash
uv sync                                   # install deps (.venv)
uv run pytest                             # all tests
uv run python scripts/check_sources.py    # validate the source manifest
uv run python scripts/init_env.py         # once: create .env with random secrets
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
