# QRIS DevGuard AI — Threat Model (Phase P0)

Status: draft · References: TRD §8, §13.3–13.4, §15, §19, §22.5

This model is written before implementation. Each control maps to a test that later phases must produce. The evidence for a control is the test listed in the table, not this document.

## 1. Assets

| Asset | Why it matters |
|---|---|
| Test key material (RSA private keys, HMAC secrets) | It is fake, but leaking it shows the redaction path is broken |
| Approval decisions | They gate the only side effect in the system (sandbox tickets) |
| Graph state, checkpoints and long-term memory | They could carry user-submitted secrets if sanitisation fails |
| Source corpus and manifest | A poisoned or outdated source produces wrong guidance |
| Service credentials (`VLLM_API_KEY`, DB, Langfuse, `MCP_AUTH_SECRET`) | They allow direct access to internal services |
| GPU pod | Exposure costs money and can lead to abuse |

## 2. Trust boundaries

```text
[ User / API client ]            (untrusted)
        │  HTTPS + bearer auth
        ▼
┌───────────────────────── private application network ─────────────────────────┐
│ FastAPI gateway ──► LangGraph worker ──► MCP server ──► validators / sandbox DB │
│        │                 │    │                                                 │
│        │                 │    └──► Qdrant (corpus = untrusted content)          │
│        │                 └──► PostgreSQL (checkpoints, memory, audit)           │
│        │                 └──► vLLM on GPU pod (private or proxied + key)        │
└────────────────────────────────────────────────────────────────────────────────┘
        │ sanitised traces only
        ▼
[ Langfuse ]                     (third party if Cloud is used)

No connection exists to any bank, ASPI, merchant or production host.
```

| # | Boundary | What crosses it | Primary control |
|---|---|---|---|
| B1 | Client → API gateway | User queries, payloads, approval decisions | AuthN, RBAC, size limits, `sanitize_input` |
| B2 | Retrieved corpus → LLM context | Document text | Treated as data; it cannot change tool permissions |
| B3 | LLM → MCP tools | Tool name and arguments | Allowlist, strict Pydantic schemas, identity binding |
| B4 | MCP → sandbox ticket table | Write action | Approval token bound to the exact payload, idempotency key |
| B5 | App → Langfuse | Traces and scores | Redaction before export; synthetic data only |
| B6 | App → vLLM pod | Prompts | SSH tunnel to vLLM bound to the pod's `127.0.0.1:8080`; no HTTP port exposed; API key; request logging off (p1-infrastructure) |

## 3. Threats and controls

| ID | Threat (STRIDE) | Boundary | Control | Verified by (later phase) |
|---|---|---|---|---|
| T1 | Unauthenticated tool or API use (S) | B1, B3 | Bearer auth on the API and the MCP server | Security test: missing or invalid auth (P7). P4: the MCP server answers 401 for no, garbage, foreign-secret and expired tokens (`tests/integration/test_mcp_server.py`); `tests/unit/test_mcp_auth.py` refuses `alg` none/HS512, wrong aud/iss, unknown role and TTL over 60 min. P5: `/api/v1/chat` takes only API-audience tokens and answers 401 for none, expired, MCP-audience and foreign-secret tokens (`tests/integration/test_api_server.py`); the agent mints its own 5-minute MCP token and never forwards the API token |
| T2 | User calls a tool beyond their role (E) | B3 | Role/tool matrix in `configs/security.yaml` | Role matrix test (P7). P4: all 16 role × tool pairs of the MCP tools (`tests/unit/test_mcp_tools.py::test_role_matrix`), and `kb_maintainer` gets `FORBIDDEN` from the real server |
| T3 | Prompt injection in user input or a document expands tool authority (E) | B2, B3 | Tools allowlisted per intent; retrieved text never in the system or instruction slot; write tool needs an approval token | 10 injection cases (P7, §22.5). P5: tools and arguments are chosen by deterministic rules, never by the model; untrusted text sits only in the user turn with brackets neutralised; an injected "call create_support_ticket" changes no tool call (`test_agent_graph.py`, `test_agent_llm.py`) |
| T4 | Approval bypass, replay, expired approval or changed payload (T, E) | B4 | Approval bound to `action_hash`, user, thread and expiry; single use | Approval bypass tests (P6/P7). P6: `create_support_ticket` re-checks the approval under a row lock and refuses a changed payload (`APPROVAL_MISMATCH`), another user or thread (`APPROVAL_INVALID`), pending/rejected/used (`APPROVAL_NOT_USABLE`) and expired (`APPROVAL_EXPIRED`) approvals (`tests/unit/test_ticket_tool.py`); only `support_engineer`/`admin` decide, never the requester (four-eyes, also a table constraint); live demo: requester 403, second decision 409 (`docs/p6-checkpoint-hitl/agent-flow-report.md`) |
| T5 | Duplicate side effect on resume or retry (T) | B4 | `idempotency_key` with a unique DB constraint | Crash/resume test (P6): API killed mid-approval and restarted, same run and approval recovered, exactly one ticket, second decision and resume 409 (`tests/integration/test_crash_resume.py`); injected crash between insert and checkpoint resumes to the same ticket (`test_agent_hitl.py`); unique `idempotency_key` constraint (`test_db_store.py`). Local only: CI has no PostgreSQL |
| T6 | Secret leakage via logs, checkpoints or traces (I) | B1, B5, B6 | Field and pattern redaction before persistence, checkpointing and tracing; prompt logging off | Secret-in-prompt test (P7/P8). P4: test keys are generated at runtime and never tracked (`test_repo_hygiene.py`: no key file, PEM block or key dir); signature results never carry a string-to-sign, key or signature; secret-shaped tool arguments are absent from responses and the call log (`test_mcp_tools.py`). P5: `sanitize_input` redacts before state and the model; a test finds no secret in checkpointer state, LLM input or the answer (`test_agent_graph.py`); API logs carry no message text. P6: `Agent.run` sanitizes before the graph because LangGraph checkpoints the raw input first (a leak found by the P6 dump); a dump of every table and checkpoint finds no secret (`test_crash_resume.py`), and a history walk guards the in-memory saver (`test_agent_hitl.py`); the ticket tool refuses secret-looking text |
| T7 | Hallucinated crypto or response-code guidance (T) | B2 | Deterministic validators; `INCONCLUSIVE` / `NOT_FOUND`; policy check blocks unsupported claims | Golden set, 0 critical hallucinations (P9). P4: the validators in `app/validators/` pass 110/110 fixtures, including `INCONCLUSIVE`, `NOT_FOUND` and `TABLE_UNAVAILABLE` cases (`scripts/eval_validators.py`, `docs/p4-deterministic-mcp-tools/validator-report.md`). P5: verdicts and sources are assembled by code, not the model; `policy_check` rules 3-5 remove valid-signature claims without `VALID`, meanings for unresolved codes, and verdicts on `INCONCLUSIVE` (`test_agent_policy.py`); rule 4 fired in the live flow 3 (`docs/p5-langgraph-agent/agent-flow-report.md`) |
| T8 | Outdated or conflicting source used silently (T) | B2 | Manifest version and approval status; conflicts shown, not merged | Scenario 4 (P5): flow 4a (requested version not in the corpus gives a clarification) passed live; flow 4b (two versions shown apart, conflict limitation) passes in CI on the synthetic conflict fixtures (`docs/p5-langgraph-agent/agent-flow-report.md`) |
| T9 | SSRF or arbitrary outbound access (I, E) | B3 | No URL-taking tools; egress allowlist | SSRF tests (P7) |
| T10 | SQL or path injection via tool arguments (T) | B3 | Typed arguments, ID regex allowlists, parameterised queries | Argument injection tests (P7). P4: path, URL, SQL, shell, newline and Windows-path strings in six identifier fields are rejected before the handler (`test_mcp_tools.py`); keys and test cases are looked up by regex-checked ID and never joined into a path |
| T11 | Cross-user access to a thread, memory or approval (I) | B1, B3 | Every lookup scoped by `owner_id`; thread ownership checked | Thread substitution test (P7). P6: thread routes answer 404 for another owner's or an unknown thread; approvals are visible only to the requester and approver roles; the ticket tool refuses another user's or thread's approval (`test_api_threads.py`, `test_ticket_tool.py`) |
| T12 | DoS or cost exhaustion on the GPU pod (D) | B1, B6 | Rate limits, token budgets, timeouts, concurrency caps | Burst test (P7) |
| T13 | Direct access to vLLM, Qdrant or Postgres (S, I) | B6 | vLLM listens only on the pod's loopback and is reached through an SSH tunnel; the pod exposes no HTTP port, so `/health`, `/metrics` and `/tokenize` (not covered by `--api-key`) are unreachable from the internet; API key kept as a second layer. Local services publish ports on `127.0.0.1` only | `tests/unit/test_compose.py` loopback-port test; `tests/unit/test_bootstrap_vllm.py` bind test (P1); port exposure check on the live pod (P1 live run) |
| T14 | Error responses leak stack traces or environment (I) | B1, B3 | Stable error envelope `code/message/correlation_id` | Tool exception tests (P7). P4: every MCP tool error is the envelope with no value, trace or environment text; a crashing handler returns `INTERNAL_ERROR` with only a correlation ID (`test_mcp_tools.py::test_internal_error_is_safe`). P5: every API error (401, 404, 422, 500) is the envelope with no submitted value or exception text (`test_api.py`) |
| T15 | Poisoned source enters the corpus (T) | B2 | Only `approved` manifest entries are ingested; a file must sit in its basis's storage area, match its type, stay under the size limit and match its manifest checksum; maintainer publish step. Retrieved text is marked untrusted on every search result | `tests/unit/test_rag_manifest.py` skip/fail cases and `tests/unit/test_rag_store_ingest.py` (P2); prompt-injection eval on retrieved text (P5/P7) |
| T16 | A public document with no published licence is redistributed through the public repo, an answer or a LinkedIn screenshot (I) | B2, repo | `reference_only` basis; files kept under the git-ignored `knowledge_base/sources/`; checker rejects a reference-only `local_path` outside it; citation excerpts capped at 300 characters (`configs/security.yaml`); the SNAP response-code table extracted in P4 stays under the git-ignored `knowledge_base/sources/derived/`, is built only after a checksum match, and its descriptions are capped at 300 characters in answers | `tests/unit/test_repo_hygiene.py` (P0); checker rule (f) unit test (P0); excerpt-cap tests in `tests/unit/test_rag_search.py` and `test_rag_citations.py` (P2); `tests/unit/test_licence_hygiene.py` scan of tracked files for ASPI text (P2, local); `test_repo_hygiene.py` fails if anything under `knowledge_base/sources/derived/` is tracked (P4) |
| T17 | A synthetic document is mistaken for an official ASPI/BI specification (S) | B2, repo | `synthetic-` ID prefix + `team_authored` basis + title marker, all enforced by the checker; marker on the document's first line; SYNTHETIC label kept on every citation | Checker rules (a)–(c) and `tests/unit/test_synthetic_docs.py` (P0); citation-label test (P5) |
| T18 | The GPU pod's public SSH port is attacked, or the bootstrap script fetched onto the pod is tampered with (S, T, E) | Pod | Key-only root login (RunPod PyTorch template); pod terminated after every session, so the port exists only while working; bootstrap script fetched by pinned commit SHA and pinned `vllm==0.32.0`; no secrets in the script (vLLM reads `VLLM_API_KEY` from a RunPod secret); pod IP, port and SSH user kept out of git | `tests/unit/test_repo_hygiene.py` IP/SSH-target test and `tests/unit/test_bootstrap_vllm.py` (P1); runbook terminate step (P1) |
| T19 | A tampered model or parser dependency runs code on the developer machine during ingestion or reranking, or changes embeddings or rerank scores silently (T, E) | Dev machine | BGE-M3 and the BGE reranker v2 M3 cross-encoder (P3) pinned by commit SHA (`configs/retrieval.yaml`); the reranker is fetched as `model.safetensors` only (no pickle) and loaded with `HF_HUB_OFFLINE=1`; every package pinned exactly in `uv.lock` (P4 adds `mcp==2.3.0`, `pyjwt==2.15.1` and `cryptography==50.0.2` as direct pins); the embedding stack (torch, transformers, sentence-transformers) is an optional extra never installed in CI; transformers 5.x loads weights with `weights_only`; ingestion and search load the model offline (`HF_HUB_OFFLINE=1`); PDF parsing uses MIT-licensed `pdfplumber` on local files only, never URLs | `tests/unit/test_rag_config.py` revision pin; `tests/integration/test_rag_cli.py` fetch dry-run prints the pinned revision (P2) and the reranker's pinned revision (P3); weekly `security-scan` pip-audit (P1) |

## 4. Residual risks and assumptions

- Langfuse is self-hosted on the developer machine with telemetry off (p1-infrastructure), so traces never leave it. The P0 egress allowlist entry for Langfuse Cloud was replaced with `localhost` in `configs/security.yaml`.
- **Setup-time egress exception (p2-knowledge-pipeline).** `scripts/fetch_models.py` downloads the pinned BGE-M3 weights from `huggingface.co` once, run by the developer. It verifies TLS against the operating system's certificate store (`truststore`), because the developer network inspects TLS. Run time stays inside `egress_allowlist`: ingestion and search load the model offline. `huggingface.co` is deliberately not added to `configs/security.yaml`, which governs the running system.
- **ASPI text in local Qdrant (p2-knowledge-pipeline).** Extracted text of `reference_only` PDFs is stored in the local Qdrant volume so search can work. It never leaves the machine except as excerpts of at most 300 characters.
- A RunPod pod may need an exposed port. In that case it must sit behind an authenticated reverse proxy with an allowlist.
- Human review of citation support is sampled, not exhaustive.
- **No separation of duties.** One person (the capstone author) signs all four P0 roles and acts as both KB maintainer and approver. The `kb_maintainer` exclusion from `approver_roles` in `configs/security.yaml` is enforced per account, not per person. This is accepted for the capstone and must change before any multi-person use.
- **Reference-only use is a risk you accept, not a licence.** The ASPI portal and its usage guide publish no terms either way (checked 2026-11-06). If terms appear later, they take precedence and the manifest entries must be re-reviewed.
