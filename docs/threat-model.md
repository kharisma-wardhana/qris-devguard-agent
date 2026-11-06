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
| B6 | App → vLLM pod | Prompts | Private network or authenticated proxy; prompt logging disabled |

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
| T13 | Direct access to vLLM, Qdrant or Postgres (S, I) | B6 | Private networking; vLLM key plus proxy, since `--api-key` does not cover every endpoint | Port exposure check (P1/P7) |
| T14 | Error responses leak stack traces or environment (I) | B1, B3 | Stable error envelope `code/message/correlation_id` | Tool exception tests (P7) |
| T15 | Poisoned source enters the corpus (T) | B2 | Only `approved` manifest entries are ingested; checksum; maintainer publish step | Ingestion validation (P2) |

## 4. Residual risks and assumptions

- Langfuse Cloud is acceptable only because all data is synthetic. Real data would require self-hosting.
- A RunPod pod may need an exposed port. In that case it must sit behind an authenticated reverse proxy with an allowlist.
- Human review of citation support is sampled, not exhaustive.
