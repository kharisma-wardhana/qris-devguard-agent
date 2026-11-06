# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

QRIS DevGuard AI is a capstone project. `docs/QRIS_DevGuard_AI_PRD_TRD.md` is the source of truth for requirements, architecture, data model, API contract and release gates. Read the relevant section before you implement anything.

Phase P0 (§27, scope and source inventory) is done, but its sign-off is still pending:
- `docs/scope.md` covers the use case, boundaries, roles and data owners.
- `docs/threat-model.md` lists threats T1–T15, each mapped to the phase that must prove its control.
- `skills/qris-integration-troubleshooting/SKILL.md` is the SOP outline.
- `knowledge_base/manifests/sources.yaml` is the source inventory, validated against `source.schema.json`.
- `configs/security.yaml` defines the role/tool matrix, redaction fields and egress allowlist.
- `configs/eval_thresholds.yaml` defines the release gates.

No application code exists yet. Phases P1 onward add it.

## Commands

```bash
uv sync                                                  # install deps into .venv
uv run pytest                                            # all tests
uv run pytest tests/unit/test_check_sources.py::test_duplicate_document_id_rejected   # single test
uv run python scripts/check_sources.py                   # validate source manifest (P0 exit check)
```

Source manifest rules: only `approved` entries may be ingested. An `approved` entry needs a real URL, a version, an effective date and confirmed permission with evidence. Never fill in versions, dates or URLs by guessing; leave them `null` until confirmed.

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
