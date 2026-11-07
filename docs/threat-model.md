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
| T1 | Unauthenticated tool or API use (S) | B1, B3 | Bearer auth on the API and the MCP server | Security test: missing or invalid auth (P7) |
| T2 | User calls a tool beyond their role (E) | B3 | Role/tool matrix in `configs/security.yaml` | Role matrix test (P7) |
| T3 | Prompt injection in user input or a document expands tool authority (E) | B2, B3 | Tools allowlisted per intent; retrieved text never in the system or instruction slot; write tool needs an approval token | 10 injection cases (P7, §22.5) |
| T4 | Approval bypass, replay, expired approval or changed payload (T, E) | B4 | Approval bound to `action_hash`, user, thread and expiry; single use | Approval bypass tests (P6/P7) |
| T5 | Duplicate side effect on resume or retry (T) | B4 | `idempotency_key` with a unique DB constraint | Crash/resume test (P6) |
| T6 | Secret leakage via logs, checkpoints or traces (I) | B1, B5, B6 | Field and pattern redaction before persistence, checkpointing and tracing; prompt logging off | Secret-in-prompt test (P7/P8) |
| T7 | Hallucinated crypto or response-code guidance (T) | B2 | Deterministic validators; `INCONCLUSIVE` / `NOT_FOUND`; policy check blocks unsupported claims | Golden set, 0 critical hallucinations (P9) |
| T8 | Outdated or conflicting source used silently (T) | B2 | Manifest version and approval status; conflicts shown, not merged | Scenario 4 (P5) |
| T9 | SSRF or arbitrary outbound access (I, E) | B3 | No URL-taking tools; egress allowlist | SSRF tests (P7) |
| T10 | SQL or path injection via tool arguments (T) | B3 | Typed arguments, ID regex allowlists, parameterised queries | Argument injection tests (P7) |
| T11 | Cross-user access to a thread, memory or approval (I) | B1, B3 | Every lookup scoped by `owner_id`; thread ownership checked | Thread substitution test (P7) |
| T12 | DoS or cost exhaustion on the GPU pod (D) | B1, B6 | Rate limits, token budgets, timeouts, concurrency caps | Burst test (P7) |
| T13 | Direct access to vLLM, Qdrant or Postgres (S, I) | B6 | vLLM listens only on the pod's loopback and is reached through an SSH tunnel; the pod exposes no HTTP port, so `/health`, `/metrics` and `/tokenize` (not covered by `--api-key`) are unreachable from the internet; API key kept as a second layer. Local services publish ports on `127.0.0.1` only | `tests/unit/test_compose.py` loopback-port test; `tests/unit/test_bootstrap_vllm.py` bind test (P1); port exposure check on the live pod (P1 live run) |
| T14 | Error responses leak stack traces or environment (I) | B1, B3 | Stable error envelope `code/message/correlation_id` | Tool exception tests (P7) |
| T15 | Poisoned source enters the corpus (T) | B2 | Only `approved` manifest entries are ingested; a file must sit in its basis's storage area, match its type, stay under the size limit and match its manifest checksum; maintainer publish step. Retrieved text is marked untrusted on every search result | `tests/unit/test_rag_manifest.py` skip/fail cases and `tests/unit/test_rag_store_ingest.py` (P2); prompt-injection eval on retrieved text (P5/P7) |
| T16 | A public document with no published licence is redistributed through the public repo, an answer or a LinkedIn screenshot (I) | B2, repo | `reference_only` basis; files kept under the git-ignored `knowledge_base/sources/`; checker rejects a reference-only `local_path` outside it; citation excerpts capped at 300 characters (`configs/security.yaml`) | `tests/unit/test_repo_hygiene.py` (P0); checker rule (f) unit test (P0); excerpt-cap tests in `tests/unit/test_rag_search.py` and `test_rag_citations.py` (P2); `tests/unit/test_licence_hygiene.py` scan of tracked files for ASPI text (P2, local) |
| T17 | A synthetic document is mistaken for an official ASPI/BI specification (S) | B2, repo | `synthetic-` ID prefix + `team_authored` basis + title marker, all enforced by the checker; marker on the document's first line; SYNTHETIC label kept on every citation | Checker rules (a)–(c) and `tests/unit/test_synthetic_docs.py` (P0); citation-label test (P5) |
| T18 | The GPU pod's public SSH port is attacked, or the bootstrap script fetched onto the pod is tampered with (S, T, E) | Pod | Key-only root login (RunPod PyTorch template); pod terminated after every session, so the port exists only while working; bootstrap script fetched by pinned commit SHA and pinned `vllm==0.32.0`; no secrets in the script (vLLM reads `VLLM_API_KEY` from a RunPod secret); pod IP, port and SSH user kept out of git | `tests/unit/test_repo_hygiene.py` IP/SSH-target test and `tests/unit/test_bootstrap_vllm.py` (P1); runbook terminate step (P1) |
| T19 | A tampered model or parser dependency runs code on the developer machine during ingestion, or changes embeddings silently (T, E) | Dev machine | BGE-M3 pinned by commit SHA (`configs/retrieval.yaml`); every package pinned exactly in `uv.lock`; the embedding stack (torch, transformers, sentence-transformers) is an optional extra never installed in CI; transformers 5.x loads weights with `weights_only`; ingestion and search load the model offline (`HF_HUB_OFFLINE=1`); PDF parsing uses MIT-licensed `pdfplumber` on local files only, never URLs | `tests/unit/test_rag_config.py` revision pin; `tests/integration/test_rag_cli.py` fetch dry-run prints the pinned revision (P2); weekly `security-scan` pip-audit (P1) |

## 4. Residual risks and assumptions

- Langfuse is self-hosted on the developer machine with telemetry off (p1-infrastructure), so traces never leave it. The P0 egress allowlist entry for Langfuse Cloud was replaced with `localhost` in `configs/security.yaml`.
- **Setup-time egress exception (p2-knowledge-pipeline).** `scripts/fetch_models.py` downloads the pinned BGE-M3 weights from `huggingface.co` once, run by the developer. It verifies TLS against the operating system's certificate store (`truststore`), because the developer network inspects TLS. Run time stays inside `egress_allowlist`: ingestion and search load the model offline. `huggingface.co` is deliberately not added to `configs/security.yaml`, which governs the running system.
- **ASPI text in local Qdrant (p2-knowledge-pipeline).** Extracted text of `reference_only` PDFs is stored in the local Qdrant volume so search can work. It never leaves the machine except as excerpts of at most 300 characters.
- A RunPod pod may need an exposed port. In that case it must sit behind an authenticated reverse proxy with an allowlist.
- Human review of citation support is sampled, not exhaustive.
- **No separation of duties.** One person (the capstone author) signs all four P0 roles and acts as both KB maintainer and approver. The `kb_maintainer` exclusion from `approver_roles` in `configs/security.yaml` is enforced per account, not per person. This is accepted for the capstone and must change before any multi-person use.
- **Reference-only use is a risk you accept, not a licence.** The ASPI portal and its usage guide publish no terms either way (checked 2026-11-06). If terms appear later, they take precedence and the manifest entries must be re-reviewed.
