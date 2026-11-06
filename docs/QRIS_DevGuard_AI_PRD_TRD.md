# QRIS DevGuard AI — Product Requirements Document (PRD) & Technical Requirements Document (TRD)

**Document status:** Draft for capstone implementation  
**Version:** 1.0  
**Date:** 5 November 2026  
**Product type:** Self-hosted Agentic AI system  
**Target environment:** RunPod GPU Pod  
**Primary users:** QRIS integration developers, technical support engineers, integration analysts  
**Owner:** Capstone project team

> **Scope and safety note:** This capstone uses public/approved technical documentation, synthetic merchant and transaction references, dummy RSA keys, and test HMAC secrets. It is not connected to a bank's production environment. Do not upload real merchant credentials, private keys, access tokens, customer data, or production logs.

---

# Part I — Product Requirements Document (PRD)

## 1. Executive Summary

QRIS DevGuard AI is a self-hosted AI developer-compliance and troubleshooting assistant for QRIS API integrations. It helps developers and technical support teams diagnose integration errors, find the relevant SNAP/QRIS documentation, validate request payloads and test signatures, and produce evidence-backed remediation guidance.

The product combines a hybrid retrieval-augmented generation (RAG) pipeline with a LangGraph agent. It does not allow the language model to independently decide whether a cryptographic value is valid: signature and payload checks are performed by deterministic validators. The LLM explains the validator output and cites the source documents used to form its recommendation.

The MVP focuses on one bounded workflow: **diagnosing QRIS API integration issues involving authentication/signatures, request formatting, timestamp construction, and response codes**.

### 1.1 Product vision

Make QRIS integration troubleshooting faster, more consistent, auditable, and safer by combining authoritative documentation with deterministic technical checks and controlled agent workflows.

### 1.2 Problem statement

Developers and support engineers may spend significant time locating the right API specification, identifying the relevant version, reconstructing the exact request/signature input, and understanding response codes. Errors can include:

- Incorrect `stringToSign` construction or canonical request formatting.
- Invalid SHA256withRSA access-token signature.
- Invalid HMAC_SHA512 transactional signature.
- Incorrect timestamp or ISO-8601 representation.
- Incorrect request headers, payload fields, or JSON formatting.
- Misinterpretation of response codes or API sequence requirements.
- Guidance sourced from an obsolete or irrelevant document version.

A general-purpose chatbot can produce plausible but unsupported answers. QRIS DevGuard AI addresses this risk through source citations, version-aware retrieval, deterministic validators, explicit uncertainty, and human approval for side-effecting actions.

### 1.3 Product goals

1. Reduce time required to locate and interpret relevant QRIS integration guidance.
2. Detect common payload, timestamp, and test-signature errors using deterministic code.
3. Ensure technical recommendations are grounded in retrievable source documents.
4. Provide repeatable, resumable agent workflows with an audit trail.
5. Demonstrate all required Senior AI Engineering Series components with measurable evidence.

### 1.4 Non-goals

- Connecting to production bank, merchant, payment, settlement, or credential systems.
- Performing real payment, refund, reversal, settlement, or merchant activation actions.
- Generating, storing, or managing production private keys, merchant secrets, or access tokens.
- Replacing official bank/ASPI documentation, compliance officers, or human support engineers.
- Guaranteeing legal or regulatory compliance based solely on an LLM response.
- Supporting every QRIS API endpoint in the first MVP.
- Autonomous execution of configuration changes or external tickets without human approval.

## 2. Target Users and Personas

| Persona | Needs | Main workflow |
|---|---|---|
| Integration Developer | Understand an integration error and verify request construction | Submit error/request sample → retrieve specification → run validator → receive cited guidance |
| Technical Support Engineer | Diagnose repeated issues consistently and prepare an escalation | Enter sanitized issue → inspect evidence and checks → draft support ticket |
| Integration Analyst / Reviewer | Confirm implementation readiness against a known specification | Select API/version → inspect requirements → review validation report |
| Knowledge Base Maintainer | Keep internal/approved technical references current | Upload approved document → review metadata → publish a versioned corpus |

## 3. Core User Journeys

### Journey A — Diagnose signature error

1. Developer opens a troubleshooting session.
2. Developer selects API family, environment (`sandbox`), and documentation version where known.
3. Developer supplies a sanitized error and a test fixture reference. Secret values are not requested.
4. Agent classifies the issue and retrieves relevant sections through hybrid search.
5. Reranker reorders candidate passages; the agent checks that retrieved evidence supports the proposed diagnosis.
6. Agent calls the appropriate deterministic validator with a fixture ID and normalized inputs.
7. The response explains the validator result, likely cause, remediation steps, and source citations.
8. If the developer asks to create an escalation ticket, the agent generates a draft and pauses for approval before creating a local sandbox ticket.

### Journey B — Validate request payload

1. Developer submits a sanitized JSON payload.
2. Agent identifies the endpoint/API type and expected schema version.
3. Agent retrieves endpoint requirements and calls `validate_request_payload`.
4. Validator returns field-level errors and warnings.
5. Agent explains each finding and cites the exact specification sections.

### Journey C — Unknown response code

1. Developer submits a response code and sanitized response body.
2. Agent calls `lookup_response_code` and searches the documentation corpus.
3. If no authoritative match exists, the agent states that the code was not found and asks for the source/API version; it must not invent a meaning.
4. Agent produces a diagnostic checklist and optional escalation draft.

### Journey D — Human-approved escalation

1. Agent prepares a ticket draft containing sanitized context, evidence, validator output, and citations.
2. LangGraph issues a human approval interrupt and persists the checkpoint.
3. An authorized user reviews and approves or rejects the proposed action.
4. On approval, the system creates a local sandbox ticket with an idempotency key; on rejection, no action is executed.
5. The decision and result are recorded in the audit trail.

## 4. MVP Functional Requirements

Priority definitions: **P0 = must ship**, **P1 = should ship if time permits**, **P2 = future enhancement**.

| ID | Priority | Requirement | Acceptance criteria |
|---|---|---|---|
| FR-01 | P0 | Chat-based troubleshooting | User can submit an issue and receive a structured diagnostic response. |
| FR-02 | P0 | Version-aware knowledge ingestion | Each source has a document ID, title, version/effective date, source URL, section/page metadata, and checksum. |
| FR-03 | P0 | Hybrid retrieval | Query combines dense semantic retrieval and BM25/full-text retrieval; returns ranked candidate passages. |
| FR-04 | P0 | Reranking | Cross-encoder reranks candidate passages; top passages are passed to the LLM. |
| FR-05 | P0 | Source citations | Every material documentation claim has at least one valid citation to document/version/section/page or URL. |
| FR-06 | P0 | Deterministic payload validation | JSON request is checked against versioned local schemas and business rules. |
| FR-07 | P0 | Deterministic test-signature validation | Test-only SHA256withRSA and HMAC_SHA512 fixtures can be checked; no production secret is required or stored. |
| FR-08 | P0 | Response-code lookup | Exact code lookup is supported; unknown values return “not found” rather than an invented explanation. |
| FR-09 | P0 | LangGraph orchestration | Workflow has state, conditional routing, tool calls, and explicit error handling. |
| FR-10 | P0 | Durable checkpointing | State is persisted by `thread_id`; interrupted or failed workflows can resume. |
| FR-11 | P0 | Human approval | Ticket creation or any write/side-effecting action pauses for approval; rejection prevents execution. |
| FR-12 | P0 | MCP server | MCP server exposes at least 3 tools with schema validation, auth, authorization, and audit logging. |
| FR-13 | P0 | SOP skill | `QRIS Integration Troubleshooting` skill encodes diagnostic sequence, escalation criteria, and evidence requirements. |
| FR-14 | P0 | Long-term memory policy | System supports explicit save/update/delete rules and excludes credentials and sensitive data. |
| FR-15 | P0 | Langfuse tracing | Each run records trace, model generation, retrieval, reranking, and tool spans with correlation IDs. |
| FR-16 | P0 | Langfuse alert | At least one alert is configured and demonstrated as firing in a controlled test. |
| FR-17 | P0 | Evaluation gate | Automated test suite returns PASS/FAIL based on explicit thresholds. |
| FR-18 | P0 | RunPod reproducibility | README explains fresh deployment, GPU profile, environment variables, data loading, and benchmark commands. |
| FR-19 | P1 | Conversation feedback | User can mark response helpful/not helpful and provide a reason. |
| FR-20 | P1 | Admin knowledge ingestion UI | Maintainer can upload, preview, validate metadata, and publish an approved document version. |
| FR-21 | P2 | OCR/document image extraction | Extract tables or scanned pages after the text-based ingestion path is stable. |
| FR-22 | P2 | IDE/Bruno/Postman integration | Provide a developer plugin or request-to-diagnostic integration. |

## 5. Agent Response Contract

Every final diagnostic response should follow a predictable structure:

1. **Summary:** one or two sentences describing the likely issue.
2. **Findings:** numbered findings separated into confirmed, likely, and unverified.
3. **Validation results:** tool name, fixture/reference used, result, and limitations.
4. **Recommended next steps:** ordered actions, each tied to evidence where applicable.
5. **Sources:** document title, version, section/page, source URL, and citation ID.
6. **Confidence/limitations:** explain missing data, stale documentation, inconclusive checks, or unsupported assumptions.

The agent must not state that a signature is valid unless the deterministic validator has verified it against a configured test fixture. It must not claim an API rule exists unless supporting source evidence was retrieved.

## 6. Business and Product Success Metrics

The following are proposed acceptance targets for the capstone, not measured results. Final report must replace them with actual test outcomes.

| Metric | MVP target | Measurement method |
|---|---:|---|
| Median time to produce a cited diagnostic | ≤ 30 seconds in the test environment | App-level end-to-end timing over fixed test set |
| Retrieval candidate Recall@20 | ≥ 90% | Gold relevant document/section labels |
| Reranked Recall@5 | ≥ 85% | Top-5 after reranking compared with gold labels |
| Citation validity | ≥ 98% | Citation IDs resolve to ingested source chunks and correct version |
| Citation support precision | ≥ 95% | Human-annotated claim-to-source support review |
| Tool execution success on valid test inputs | ≥ 95% | Deterministic tool test suite |
| Critical hallucination rate on golden set | 0 | Review unsupported/incorrect claims in critical test cases |
| Approval enforcement | 100% | All side-effecting tests pause before execution |
| Successful standard agent runs | 4/4 | Four scripted scenario runs |
| Crash-and-resume | Pass | Resume from saved checkpoint with no duplicate side effect |
| Unauthorized MCP side effects | 0 | Security test suite |
| Langfuse alert | 1 alert demonstrably firing | Controlled latency or boolean-score threshold test |

## 7. Scope Boundaries and Assumptions

- Initial corpus is restricted to approved SNAP QRIS technical references, internal SOP excerpts approved for use, response-code references, and curated test fixtures.
- All uploaded source material must have permission for use in the capstone.
- API behavior is versioned. If source documents conflict, the assistant presents the conflict and asks for clarification rather than silently choosing one.
- MVP validators operate locally with test key material and synthetic request examples.
- The ticket tool writes to a local sandbox database/table only.
- The system can operate with one GPU Pod and a small CPU service stack; scale-out is not a capstone requirement.
- Exact GPU type, model quantization, context length, and batch settings are finalized after a short benchmark on available RunPod inventory.

## 8. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Incorrect or outdated source document | Incorrect recommendation | Version metadata, source checksum, effective date, corpus review, conflicting-version warning |
| Hallucinated cryptographic guidance | Security/integration failure | Deterministic validator, cited evidence, no unsupported signature claims, golden tests |
| Prompt injection inside documents or user input | Unsafe tool behavior | Treat retrieved text as untrusted data; separate instructions from evidence; allowlisted tools and arguments |
| Secret leakage through traces | Credential compromise | Reject/redact secrets, disable raw body logging, sanitize before tracing, synthetic test data only |
| MCP tool misuse | Unauthorized side effects | AuthN/AuthZ, least privilege, JSON Schema validation, approval gate, idempotency key |
| RunPod Pod interruption | Lost model/data/work | Persist repository artifacts and data on network volume or external backup; checkpoint database on persistent storage |
| Model too slow or exceeds VRAM | Poor demo experience | Benchmark quantized model alternatives and adjust max context/concurrency |
| Poor retrieval due to corpus quality | Low answer quality | Curate golden Q&A set, tune chunking/metadata, measure retrieval before tuning prompts |

---

# Part II — Technical Requirements Document (TRD)

## 9. Proposed Technology Stack

Use a pinned dependency set in the repository (`uv.lock` or equivalent). The following is the recommended architecture; exact patch versions must be resolved and tested at implementation time.

| Layer | Recommended technology | Responsibility |
|---|---|---|
| API backend | Python 3.11+ / FastAPI / Pydantic | HTTP API, validation, auth boundary |
| Agent orchestration | LangGraph | Stateful workflow, conditional edges, checkpointing, interrupts |
| LLM serving | vLLM OpenAI-compatible server | Self-hosted text generation on RunPod GPU |
| Baseline LLM | Qwen-family instruct model, 7B–8B class where supported | Reasoning and response generation; exact checkpoint/license to be confirmed |
| Embeddings | Multilingual embedding model (e.g. BGE-M3 class) | Dense retrieval for Indonesian/English technical material |
| Reranker | Multilingual cross-encoder (e.g. BGE reranker v2 M3 class) | Candidate re-scoring |
| Vector/search store | Qdrant | Dense vectors and BM25/full-text hybrid retrieval |
| Relational DB | PostgreSQL | Users, sessions, checkpoint store, memory, ticket drafts, audit log |
| MCP server | Python MCP SDK / FastMCP-compatible implementation | Typed tools exposed to LangGraph agent |
| Observability | Langfuse | LLM traces, retrieval/tool spans, scores, dashboards, alerts |
| Containerization | Docker Compose for local services; RunPod Pod for GPU serving | Reproducible deployment |
| Testing | pytest, httpx, security test scripts | Unit, integration, eval, and MCP security tests |

**Model selection rule:** do not select a model only by parameter count. Verify license, Indonesian/English technical performance, tool-call reliability, context support, compatibility with the installed vLLM version, and actual RunPod VRAM usage. The baseline model name is configurable and must be recorded in the final report.

## 10. High-Level Architecture

```mermaid
flowchart TD
    U[Developer / Support Engineer] --> UI[Web UI or API Client]
    UI --> API[FastAPI Gateway]
    API --> AUTH[Authentication and RBAC]
    AUTH --> LG[LangGraph Orchestrator]

    LG --> CLASSIFY[Intent and Risk Classification]
    CLASSIFY --> RET[Hybrid Retriever]
    RET --> QD[(Qdrant: BM25 + Dense)]
    RET --> RERANK[Cross-Encoder Reranker]
    RERANK --> EVID[Evidence and Version Check]
    EVID --> LLM[vLLM OpenAI-Compatible API]
    LLM --> ROUTE[Tool / Answer Routing]

    ROUTE --> MCP[MCP Client]
    MCP --> M1[Payload Validator]
    MCP --> M2[Test Signature Validator]
    MCP --> M3[Response Code Lookup]
    MCP --> M4[Spec Search]
    MCP --> M5[Sandbox Ticket Draft]

    ROUTE --> HITL{Human Approval Required?}
    HITL -- Yes --> INT[LangGraph Interrupt + Checkpoint]
    INT --> APPROVE[Authorized Reviewer]
    APPROVE -->|Approve| EXEC[Idempotent Sandbox Action]
    APPROVE -->|Reject| NOOP[No Side Effect]
    HITL -- No --> RESP[Response Composer]
    EXEC --> RESP
    NOOP --> RESP

    RESP --> API
    LG <--> PG[(PostgreSQL: Checkpoint, Memory, Audit)]
    LG --> LF[Langfuse Traces, Scores, Alerts]
```

### 10.1 Deployment boundaries

- **GPU Pod:** vLLM model server; optionally embedding/reranking if the GPU has sufficient VRAM and the benchmark supports co-location.
- **CPU application services:** FastAPI, LangGraph worker, MCP server, Qdrant, PostgreSQL, and Langfuse integration.
- **Network boundary:** only the API gateway is exposed publicly. vLLM, Qdrant, PostgreSQL, and MCP remain private to the application network where possible.
- **Persistence:** database and knowledge files are stored on persistent volumes or backed up outside ephemeral container storage.

## 11. LangGraph Agent Design

### 11.1 State schema

Minimum state fields:

```python
class DevGuardState(TypedDict):
    thread_id: str
    user_id: str
    user_query: str
    sanitized_context: dict
    intent: str
    risk_level: str
    api_family: str | None
    requested_spec_version: str | None
    retrieved_chunks: list[dict]
    reranked_chunks: list[dict]
    validator_results: list[dict]
    tool_calls: list[dict]
    draft_action: dict | None
    approval_status: str | None  # not_required | pending | approved | rejected
    final_answer: dict | None
    error: dict | None
    idempotency_key: str | None
```

Do not put raw secrets, private keys, access tokens, or full production request bodies in the persisted graph state. Only store sanitized values, fixture IDs, masked snippets, or hashes required for traceability.

### 11.2 Graph nodes

| Node | Responsibility | Failure behavior |
|---|---|---|
| `sanitize_input` | Redact likely secrets and validate input size/type | Reject or request sanitized resubmission |
| `classify_intent` | Identify signature, payload, timestamp, response-code, or general-doc query | Fall back to `general_troubleshooting` |
| `classify_risk` | Decide whether the workflow could cause an external/write action | Default to high risk if ambiguous |
| `retrieve_evidence` | Hybrid search and collect candidate chunks | Return no-evidence state; do not hallucinate |
| `rerank_evidence` | Cross-encoder reranking | Fall back to fused retrieval order and lower confidence |
| `validate_evidence` | Check source version, citations, and evidence coverage | Block unsupported factual claims |
| `select_tools` | Choose allowlisted MCP tools | Reject unknown tools |
| `execute_read_tools` | Run read-only or deterministic validators | Retry only bounded transient failures |
| `compose_diagnosis` | Generate structured answer with citations | Return controlled error if generation fails |
| `policy_check` | Check claims, secrets, risk, and citations | Remove unsafe content or abstain |
| `request_human_approval` | Interrupt before side effect | Persist pending approval state |
| `execute_approved_action` | Perform approved sandbox action once | Use idempotency key; record result |
| `memory_decision` | Apply save/update/delete policy | Default to not saving |
| `finalize` | Persist result and emit Langfuse scores | Ensure response includes run ID |

### 11.3 Routing logic

- A read-only question can proceed from retrieval to answer composition.
- A validator request must call the appropriate deterministic tool before a definitive validation statement is generated.
- Any write/side-effecting request routes to `request_human_approval`.
- Missing/ambiguous evidence routes to an abstention or clarification response.
- High-risk or malformed requests must not reach an execution tool.

### 11.4 Checkpointing and crash recovery

- Compile the graph with a durable PostgreSQL checkpointer.
- Every run has a cryptographically random `thread_id` and a unique `run_id`.
- Persist checkpoints at graph super-step boundaries.
- Resume using the same `thread_id` and the appropriate resume command after interrupt or recoverable failure.
- Run an explicit crash test: stop the agent after a saved checkpoint and before an approved action; restart service; resume; verify state continuity.
- Side effects must be idempotent. Use `idempotency_key = hash(thread_id + action_type + normalized_action_payload)` and enforce a unique DB constraint.
- Do not rely on a repeated thread ID alone to guarantee exactly-once execution; enforce idempotency at the action boundary.

## 12. RAG and Knowledge Ingestion Design

### 12.1 Source metadata

Every source must contain:

```json
{
  "document_id": "snap-qris-security-guide",
  "title": "SNAP QRIS Security Guide",
  "source_url": "https://example.org/approved-source",
  "publisher": "Source organization",
  "document_version": "version-or-revision",
  "effective_date": "YYYY-MM-DD",
  "ingested_at": "ISO-8601 timestamp",
  "language": "id|en",
  "page_or_section": "section/page label",
  "checksum_sha256": "...",
  "approval_status": "approved"
}
```

`example.org` is a schema placeholder, not a source to ingest. Store the real approved source URL and metadata at ingestion time.

### 12.2 Ingestion pipeline

1. Fetch or upload approved document.
2. Verify MIME type, file size, and source permission.
3. Extract text and page/section boundaries.
4. Normalize whitespace while preserving code samples, JSON, headers, signature examples, and meaningful punctuation.
5. Split by structural sections first; use token-based chunking only within long sections.
6. Attach document/version/page/section metadata to each chunk.
7. Compute SHA-256 checksum and deduplicate unchanged content.
8. Generate dense embeddings and upsert to Qdrant.
9. Build BM25/full-text index.
10. Run ingestion validation and sample retrieval tests before publishing.

Recommended initial chunking: 400–800 tokens with 60–100 token overlap, adjusted using retrieval evaluation. Avoid splitting canonical request examples, code blocks, endpoint tables, or signature formulas across chunks.

### 12.3 Retrieval pipeline

1. Normalize query without removing identifiers, endpoint paths, response codes, header names, or cryptographic algorithm names.
2. Run dense semantic retrieval and BM25/full-text retrieval in parallel.
3. Fuse candidate lists using Reciprocal Rank Fusion (RRF) as the initial default.
4. Retrieve top 20–30 candidates.
5. Rerank with a multilingual cross-encoder.
6. Select top 5–8 passages subject to context budget and source-version consistency.
7. Pass source references to the LLM and enforce citation output schema.

Qdrant supports dense and BM25/full-text prefetches and fusion approaches such as RRF; the fusion choice should be evaluated on a labeled query set rather than tuned by intuition. Source: https://qdrant.tech/documentation/hybrid-queries/

### 12.4 Citation contract

Each cited evidence item should include:

```json
{
  "citation_id": "CIT-000123",
  "document_id": "snap-qris-security-guide",
  "document_version": "version-or-revision",
  "title": "Document title",
  "page": 12,
  "section": "4.2 Signature Generation",
  "chunk_id": "uuid",
  "source_url": "https://approved-source.example/path",
  "quote_or_excerpt": "Short supporting excerpt"
}
```

Citation validation checks:
- Citation ID exists in the current retrieval result.
- Source document is approved and not superseded for the requested version.
- Page/section metadata resolves.
- The excerpt supports the statement (human-labeled during eval; automated checks may assist but do not replace review).
- The answer does not cite a source that was not retrieved for that run.

### 12.5 Version conflict handling

If two active documents disagree:
- Prefer an explicitly selected version only when user context identifies it.
- Otherwise show both source versions and describe the conflict.
- Do not silently merge incompatible rules.
- Escalate to knowledge maintainer for source-of-truth confirmation.

## 13. MCP Server Specification

### 13.1 Transport and network policy

- Use the MCP transport and protocol version supported by the pinned SDK used in the capstone; record the exact SDK and protocol version in the README.
- Prefer Streamable HTTP for remote/container deployment where supported; stdio may be used for local development.
- MCP endpoint is private and reachable only from the trusted agent service or through an authenticated gateway.
- Never expose raw database, shell, filesystem, or unrestricted HTTP tools to the model.

### 13.2 Minimum MCP tools

| Tool | Access | Input | Output |
|---|---|---|---|
| `search_qris_spec` | Read-only | `query`, optional `api_family`, `version`, `top_k` | Ranked source passages with citation metadata |
| `validate_request_payload` | Deterministic/read-only | `api_family`, `endpoint_id`, `spec_version`, sanitized `payload` | `valid`, field errors, warnings, schema version |
| `validate_test_signature` | Deterministic/read-only | `algorithm`, `fixture_id`, `canonicalization_profile`, `test_payload_ref` | Valid/invalid, expected fixture profile, diagnostic code; never return secret material |
| `lookup_response_code` | Read-only | `code`, `api_family`, `spec_version` | Exact match or explicit not-found result with citation |
| `create_support_ticket` | Write; approval required | `idempotency_key`, sanitized title/body, evidence IDs, priority | Sandbox ticket ID and state |

At least three tools are mandatory; the recommended MVP exposes all five.

### 13.3 Tool safety rules

- Validate every argument against a strict JSON Schema/Pydantic model.
- Apply maximum lengths, enumerations, type checks, and allowlists.
- Bind every tool call to authenticated user identity and role.
- Read-only tools must not mutate state.
- `create_support_ticket` requires an approval token/state produced by the human-approval workflow.
- Deny if approval is missing, expired, belongs to another user/thread, or does not match the exact action payload.
- Add an idempotency key and unique DB constraint to prevent duplicate ticket creation.
- Use least-privilege DB credentials per tool.
- Do not accept arbitrary URL, SQL, shell, Python, or filesystem paths from the model.
- Never forward a client token to a downstream service as-is. Validate audience and authorization; use a separate server-side credential for an upstream resource if one is ever integrated.
- Return safe error messages; do not include stack traces, secret values, or environment variables.

### 13.4 P7-style go-live checklist

This is the project's proposed go-live checklist. If the course provides a named P7 checklist, map each item to that official checklist and attach the required evidence.

| Control | Evidence required | Pass condition |
|---|---|---|
| Authentication | Unauthorized request tests | Requests without valid identity are rejected |
| Authorization | Role/tool matrix tests | User cannot invoke tools outside assigned permissions |
| Input validation | Invalid schema/fuzz test results | Malformed arguments are rejected before handler execution |
| Least privilege | DB/service account configuration | Tool can access only required tables/actions |
| Secret management | Environment/secret scan | No secrets in source, image, response, or logs |
| Transport security | TLS/proxy configuration | External access uses HTTPS; internal ports are not public |
| Auditability | MCP call audit logs | Actor, tool, timestamp, outcome, and correlation ID recorded |
| Rate limiting | Burst test | Excessive calls are throttled |
| Timeout/retry | Failure injection | Bounded timeout and retry; no retry of unsafe write without idempotency |
| Error handling | Tool exception tests | Safe structured errors; no stack trace leakage |
| Prompt injection defense | Malicious user/document test | Retrieved instructions cannot expand tool authority |
| SSRF protection | URL/redirect/private-IP test | Arbitrary outbound URL access is denied |
| State isolation | Cross-user/thread test | One user cannot read or modify another user's state |
| Human approval | Approval bypass test | Side effect cannot occur without valid approval |
| Dependency hygiene | Dependency and container scan | Known critical vulnerabilities are remediated or documented with mitigations |
| Observability | Trace/log correlation test | Every tool call is traceable without logging secrets |

## 14. Deterministic Validator Requirements

### 14.1 Payload validator

- Use versioned JSON Schema files for each supported endpoint and API version.
- Validate required fields, type, enum, formats, length, nested objects, and allowed additional properties.
- Add business-rule checks separately from JSON Schema so that the rule source/version can be audited.
- Return machine-readable error codes and JSON pointers to the invalid field.
- Do not silently mutate or auto-correct submitted payloads in MVP.

### 14.2 Signature validator

- Use test-only RSA private/public key fixtures and test-only HMAC secrets stored as development secrets or fixture files outside version control.
- Support only explicitly configured algorithm profiles.
- Implement canonicalization as a named, versioned function; do not let the LLM construct or modify canonical strings.
- Test positive and negative fixtures, including whitespace, character encoding, field ordering, timestamp, and tampering variations.
- Redact signature values and key material from logs, errors, and traces.
- Never request or accept production private keys, live secrets, access tokens, or merchant credentials.
- Return `INCONCLUSIVE` when required context or fixture metadata is missing; do not guess.

### 14.3 Response-code lookup

- Exact-match lookup by API family and version.
- If the code is unknown, return `NOT_FOUND` with available metadata; do not semantically invent a response-code definition.
- Similar-code suggestions may be shown only as suggestions and must be labeled as such.

## 15. Human-in-the-Loop and Risk Policy

### 15.1 Risk classes

| Risk | Examples | Policy |
|---|---|---|
| Low | Search docs, explain a response code, validate a sanitized local fixture | May execute automatically |
| Medium | Generate a proposed code/configuration change or escalation draft | Draft only; human review recommended |
| High | Create/submit ticket, modify a record, trigger retry, change integration configuration | Mandatory approval before execution |
| Prohibited in MVP | Payment, refund, reversal, production credential operation, merchant activation, production API call | Tool must not exist or must hard-deny |

### 15.2 Approval requirements

Approval request must show:
- Exact action and target.
- Sanitized parameters and supporting evidence.
- Expected side effect.
- Approver identity and timestamp.
- Expiration time and one-time approval ID.

Approval must be bound to the exact action payload. Any payload change invalidates the approval and requires a new review.

## 16. Long-Term Memory Requirements

Long-term memory is separate from LangGraph checkpoint state and must have its own policy and storage table.

### 16.1 Memory categories

| Category | Save? | Example |
|---|---|---|
| User preference | Yes, when explicit and useful | Preferred language or preferred diagnostic output format |
| Project context | Yes, if non-sensitive and durable | User is working on a sandbox QRIS integration demo |
| Verified troubleshooting pattern | Yes, after reviewer confirmation and sanitization | A recurring test-fixture issue and its verified resolution |
| Temporary conversation detail | No by default | One-off error message or transient debugging state |
| Credentials/secrets | Never | Private key, HMAC secret, token, password |
| Personal/merchant/customer data | Never in MVP | Names, phone numbers, account IDs, raw transaction identifiers |
| Unverified agent inference | Never | Suspected root cause not confirmed by a human or validator |

### 16.2 Save/update/delete rules

- **Save:** only explicit user preference or non-sensitive, durable information with a clear future usefulness. Verified patterns require reviewer approval and sanitization.
- **Update:** update only when new information is explicit and more current; keep version history for audit when appropriate.
- **Delete:** honor user/admin deletion request; delete from active memory and scheduled backups according to the configured retention policy.
- **Expiry:** MVP default 90 days for operational memory, subject to the actual unit policy. User preferences can use a separate review/expiry date.
- **Default behavior:** do not save. The graph must call a memory decision node and record the reason for save/no-save.
- **Isolation:** scope memory by user or authorized team; never use one user's private memory for another user.

### 16.3 Suggested schema

`long_term_memory(id, owner_id, memory_type, content_sanitized, source_run_id, confidence, approved_by, created_at, updated_at, expires_at, deleted_at, version)`

## 17. Data Model

Core PostgreSQL entities:

| Table | Purpose | Important fields |
|---|---|---|
| `users` | Identity and roles | `id`, `email_or_subject`, `role`, `created_at` |
| `agent_threads` | Conversation/thread metadata | `thread_id`, `owner_id`, `created_at`, `updated_at`, `status` |
| `checkpoints` / LangGraph checkpoint tables | Durable graph state | Managed by LangGraph checkpointer migrations |
| `documents` | Source registry | `document_id`, `version`, `source_url`, `checksum`, `approval_status` |
| `document_chunks` | Chunk registry and citation metadata | `chunk_id`, `document_id`, `page`, `section`, `text_hash` |
| `validator_runs` | Deterministic validation audit | `run_id`, `tool_name`, `fixture_id`, `result_code`, `created_at` |
| `tool_audit_logs` | MCP audit trail | `actor_id`, `tool_name`, `input_hash`, `result_status`, `correlation_id` |
| `approval_requests` | Human approval state | `approval_id`, `thread_id`, `action_hash`, `status`, `approver_id`, `expires_at` |
| `sandbox_tickets` | Local-only support ticket records | `ticket_id`, `idempotency_key`, `title`, `body_sanitized`, `status` |
| `long_term_memory` | Approved durable memory | Fields described in Section 16.3 |
| `eval_runs` | Evaluation history | `eval_id`, `dataset_version`, `gate_status`, `metrics_json` |

Do not persist raw credentials or unredacted secrets in any table.

## 18. Application API Contract

All endpoints are versioned under `/api/v1`. Use bearer authentication or an equivalent development identity provider; enforce RBAC in the API and again at the tool boundary.

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/v1/threads` | Create new agent thread |
| `POST` | `/api/v1/threads/{thread_id}/messages` | Submit troubleshooting query |
| `GET` | `/api/v1/threads/{thread_id}` | Read thread status and latest response |
| `POST` | `/api/v1/threads/{thread_id}/resume` | Resume an interrupted run with approval/rejection |
| `GET` | `/api/v1/approvals/{approval_id}` | Retrieve pending approval details |
| `POST` | `/api/v1/approvals/{approval_id}/decision` | Approve or reject exact action |
| `POST` | `/api/v1/feedback` | Submit response feedback |
| `POST` | `/api/v1/admin/documents` | Upload/register approved source |
| `POST` | `/api/v1/admin/documents/{document_id}/publish` | Publish validated document version |
| `GET` | `/health/live` | Process liveness |
| `GET` | `/health/ready` | Dependency/readiness check |

### 18.1 Example chat request

```json
{
  "message": "Why does my sandbox test signature fail?",
  "context": {
    "api_family": "QRIS",
    "environment": "sandbox",
    "spec_version": "known-version-or-null",
    "fixture_id": "rsa-test-fixture-001"
  }
}
```

### 18.2 Example response

```json
{
  "thread_id": "thread-uuid",
  "run_id": "run-uuid",
  "status": "completed",
  "diagnosis": {
    "summary": "The test signature does not match the configured fixture profile.",
    "findings": [],
    "validator_results": [],
    "recommended_steps": [],
    "citations": [],
    "confidence": "medium",
    "limitations": ["The check was run against a synthetic fixture only."]
  }
}
```

### 18.3 Error format

Use a stable error envelope: `code`, `message`, `correlation_id`, and optional `field_errors`. Never return stack traces, secrets, or environment configuration.

## 19. Security, Privacy, and Data Handling

### 19.1 Security baseline

- Apply authentication and role-based authorization to the API and MCP server.
- Keep vLLM, Qdrant, PostgreSQL, and MCP on private networking; expose only the minimum required public ingress.
- Use TLS for externally reachable services.
- Store secrets in RunPod secrets/environment injection or a dedicated secret manager; do not commit `.env` files.
- Run containers as non-root where possible; pin image tags and dependencies.
- Validate and sanitize uploaded files; reject unsupported formats and oversized inputs.
- Treat user input and retrieved documents as untrusted data; never allow retrieved text to change tool permissions.
- Use outbound network allowlists; block cloud metadata/private IP ranges unless explicitly needed.
- Disable prompt/body logging in vLLM and application logs unless sanitized test-only debugging is explicitly enabled.
- Scrub Langfuse inputs/outputs before export; only synthetic data is permitted in the capstone project.
- Record audit metadata without recording secrets or entire sensitive payloads.

### 19.2 Secret redaction

Redact fields matching patterns such as `Authorization`, `access_token`, `client_secret`, `private_key`, `secret`, `password`, `signature`, and configured merchant credential fields. Redaction must occur before database persistence, checkpoint serialization, and Langfuse tracing.

### 19.3 Retention defaults

- Raw user message: do not store if rejected for containing secrets; otherwise store sanitized version only.
- Graph checkpoint: 30 days for capstone unless course requirements or debugging needs require a shorter/longer period.
- Application audit events: 90 days in the capstone environment.
- Long-term memory: 90-day expiry by default, with explicit delete support.
- Test fixtures: retained for reproducible evaluation; no live credentials.

These are proposed technical defaults, not a statement of legal retention requirements. Production use must be reviewed against the organization's security policy and applicable Indonesian data-protection requirements.

## 20. vLLM Model Serving Requirements

### 20.1 Serving contract

- Serve the configured instruct model via vLLM's OpenAI-compatible `/v1/chat/completions` API.
- Keep the model identifier, quantization, dtype, maximum model length, and GPU profile in versioned deployment configuration.
- Require an API key or private gateway authentication; do not assume the vLLM `--api-key` protects every non-`/v1` endpoint. Restrict access at the reverse proxy/network layer as well.
- Health check the model endpoint before enabling the agent.
- Use bounded request timeouts, token budgets, and concurrency.
- Disable unnecessary request-body/prompt logging.

### 20.2 Illustrative launch command

The exact flags depend on the selected model, vLLM version, GPU, and quantization. Validate the final command using the pinned container image.

```bash
vllm serve "$MODEL_ID" \
  --host 0.0.0.0 \
  --port 8000 \
  --api-key "$VLLM_API_KEY" \
  --dtype auto \
  --max-model-len "$MAX_MODEL_LEN"
```

Keep port 8000 private where possible. If RunPod requires an exposed port for connectivity, protect it with a reverse proxy/API gateway, strong authentication, and an allowlist. vLLM documentation notes that API-key authentication does not cover every endpoint, so network hardening is required. Reference: https://docs.vllm.ai/en/stable/serving/online_serving/openai_compatible_server/

### 20.3 GPU sizing strategy

Do not hard-code a GPU type before model quantization and context length are benchmarked. Use this selection sequence:

1. Start with the smallest available GPU that satisfies the chosen model's VRAM requirement plus KV cache headroom.
2. Run one-request and concurrent-request tests at the intended input/output token lengths.
3. Record peak VRAM, throughput, TTFT, and TPOT.
4. If memory is insufficient, reduce context length/concurrency, use a supported quantization, or choose a larger GPU.
5. Record the exact RunPod GPU SKU and vLLM container/model revisions in the final report.

Suggested capstone baseline: one 7B–8B instruct model with a quantized or reduced-precision configuration where supported. A 24 GB class GPU may be sufficient for some quantized deployments; a 48 GB class GPU offers more headroom. This is a planning range, not a guarantee—measure on the chosen model.

### 20.4 Required inference benchmark metrics

- TTFT: time to first generated token.
- TPOT: time per output token after the first token.
- End-to-end latency p50/p95.
- Input/output token counts.
- Requests per second under a fixed workload.
- Peak GPU VRAM and utilization.

Use `vllm bench serve` with a fixed dataset/workload and record model revision, GPU, concurrency, prompt length, output length, warmup, and cache settings. Ensure the benchmark does not accidentally rely on repeated-prefix cache behavior unless that is the intended test. Reference: https://docs.vllm.ai/en/latest/benchmarking/cli/

## 21. Langfuse Observability and Alerting

### 21.1 Trace structure

One trace per user request. Create child spans/generations for:
- input sanitization;
- intent/risk classification;
- dense retrieval;
- BM25 retrieval and fusion;
- reranking;
- MCP tool call(s);
- LLM generation;
- human approval wait/action;
- final policy/evidence check.

Record `thread_id`, `run_id`, model name/revision, prompt version, retrieval corpus version, tool name, latency, token usage, and sanitized status. Do not include secrets or raw merchant data.

### 21.2 Scores

Minimum scores:
- `citation_valid` (boolean)
- `answer_supported` (boolean)
- `tool_success` (boolean)
- `policy_pass` (boolean)
- `user_feedback` (categorical or numeric)

### 21.3 Mandatory alert

Create at least one alert that can be demonstrated during evaluation. Recommended:

**Alert name:** `devguard-agent-p95-latency-high`  
**Source:** Observations  
**Metric:** p95 latency  
**Filter:** environment = `capstone` and service = `agent`  
**Threshold:** initial test threshold set deliberately low enough to trigger during a controlled slow-request test; final operational threshold calibrated from baseline measurements.  
**Test:** inject a controlled delay into a non-production test node, run enough requests for the alert window, and capture the alert state/notification.

Alternative: alert when the boolean `policy_pass` score rate falls below the configured threshold. Record the actual alert condition, window, threshold, and screenshot.

Langfuse supports alerts over observations and numeric/categorical/boolean scores, including latency-based and custom error signals. References: https://langfuse.com/docs/observability/features/alerts and https://langfuse.com/docs/observability/overview

## 22. Evaluation Suite and Release Gates

### 22.1 Golden dataset

Create a versioned dataset with at least:
- 30 retrieval queries with gold relevant document sections.
- 20 payload validation fixtures (valid and invalid).
- 20 signature fixtures (positive and negative, test-only).
- 15 response-code queries including unknown codes.
- 10 prompt-injection/tool-abuse cases.
- 4 end-to-end scripted agent runs matching the report requirements.

Include Indonesian and English queries where applicable. Split tuning and test sets to avoid tuning and reporting on the same samples.

### 22.2 Retrieval metrics

Measure:
- Dense Recall@5/10/20.
- BM25 Recall@5/10/20.
- Hybrid fused Recall@5/10/20.
- Reranked Recall@5 and MRR@5/nDCG@5.

For the required before/after report, compare the initial fused top-5 against the reranked top-5 selected from the same top-20 candidate pool. Also report candidate Recall@20 separately; reranking cannot recover a relevant document that was not retrieved into the candidate pool.

### 22.3 Agent evaluation cases

| Run | Scenario | Expected behavior |
|---|---|---|
| 1 | Invalid test RSA signature | Retrieve correct security section, call test validator, explain failure with citations |
| 2 | Incorrect timestamp/payload field | Call payload validator, identify field/path, cite schema/spec |
| 3 | Unknown response code | Exact lookup returns not found; agent does not invent a meaning |
| 4 | Old specification version | Detect version ambiguity/conflict and ask for version or explain both sources |

### 22.4 Crash/resume test

1. Start one run and allow graph state to reach a persisted checkpoint.
2. Trigger an interrupt before a sandbox ticket action, or stop the worker after the checkpoint.
3. Restart the agent service.
4. Resume using the same thread/checkpoint context.
5. Approve the exact pending action.
6. Confirm one and only one sandbox ticket is created.
7. Confirm audit log and Langfuse trace show the original run context and resumed execution.

### 22.5 MCP security test set

At minimum test:
- Missing/invalid authentication.
- Authenticated user without permission for write tool.
- Malformed JSON and schema bypass attempts.
- Oversized input and excessive request rate.
- Prompt injection asking the model to ignore policy or call a hidden tool.
- Tool argument injection in identifiers/strings.
- SQL-like payloads and path traversal strings.
- SSRF attempts including localhost, private IP, link-local, and redirect targets.
- Cross-user/thread ID substitution.
- Approval replay, expired approval, and changed action payload.
- Duplicate action replay with same idempotency key.
- Secret in prompt to verify redaction before logs/traces/checkpoints.

Pass condition: **zero unauthorized side effects**, no secret leakage, and every denied request returns a safe structured error. The test report must list each payload, expected result, actual result, and evidence.

### 22.6 Release gate

The build is **PASS** only when all P0 gates pass. No average score may hide a critical failure.

| Gate | PASS threshold |
|---|---|
| RAG candidate coverage | Recall@20 ≥ 90% |
| Post-rerank ranking | Recall@5 ≥ 85%; MRR@5 and nDCG@5 reported |
| Citation validity | ≥ 98% |
| Citation support precision | ≥ 95% on human-reviewed sample |
| Deterministic validators | 100% of positive/negative fixtures return expected result |
| Golden answer quality | ≥ 90% supported/correct; zero critical unsafe claims |
| Agent scenario runs | 4/4 expected outcomes |
| Crash/resume | Successful resume and no duplicate side effect |
| MCP security | Zero unauthorized side effects; no secret leakage |
| HITL | 100% of high-risk actions require approval |
| Langfuse | Required spans present and one alert proven firing |
| Reproducibility | Clean deployment succeeds from README instructions |

Any failure in secret leakage, unauthorized side effect, approval bypass, or fabricated critical cryptographic guidance is an automatic FAIL regardless of aggregate score.

## 23. Non-Functional Requirements

| Area | Requirement |
|---|---|
| Availability | Capstone demo target ≥ 95% during scheduled test window; not a production SLA |
| Latency | Initial target: p95 TTFT ≤ 5s and p95 TPOT ≤ 120ms under a documented single-request baseline; report actual values and hardware |
| Reliability | Recover from worker restart using durable checkpoint; idempotent write actions |
| Security | Private service ports, auth/RBAC, input validation, safe logs, secrets redaction |
| Privacy | Synthetic fixtures only; sanitized traces and state |
| Maintainability | Typed models, modular services, pinned dependencies, migrations, test coverage |
| Reproducibility | One documented deployment path and fixed dataset/model/config revisions |
| Explainability | Citations and deterministic validation evidence for material technical claims |
| Resource control | Max input/output tokens, concurrency caps, timeouts, retry budgets |

Latency thresholds are proposed benchmark targets, not guaranteed RunPod performance. Final thresholds should be adjusted only with a documented baseline and workload.

## 24. Repository Structure

```text
qris-devguard-ai/
├── README.md
├── LICENSE
├── pyproject.toml
├── uv.lock
├── .env.example
├── docker-compose.yml
├── Makefile
├── configs/
│   ├── app.yaml
│   ├── model.yaml
│   ├── retrieval.yaml
│   ├── security.yaml
│   └── eval_thresholds.yaml
├── docs/
│   ├── PRD_TRD.md
│   ├── architecture.md
│   ├── threat-model.md
│   ├── runpod-deployment.md
│   └── operations-runbook.md
├── app/
│   ├── api/                 # FastAPI routers and request/response schemas
│   ├── agent/               # LangGraph state, nodes, routing, graph builder
│   ├── rag/                 # ingestion, chunking, embeddings, hybrid search, reranker
│   ├── mcp_server/          # MCP tools, schemas, authz, audit
│   ├── validators/          # payload, test signature, timestamp, response code
│   ├── memory/              # save/update/delete policy and repository
│   ├── observability/       # Langfuse callbacks, sanitization, score emitters
│   ├── security/            # auth, RBAC, redaction, policy enforcement
│   └── db/                  # SQLAlchemy models, migrations, repositories
├── skills/
│   └── qris-integration-troubleshooting/
│       ├── SKILL.md
│       └── references/
├── knowledge_base/
│   ├── sources/              # approved source material; keep access-controlled
│   ├── manifests/
│   └── fixtures/
│       ├── payloads/
│       └── signatures/
├── evals/
│   ├── datasets/
│   ├── retrieval/
│   ├── agent_scenarios/
│   ├── mcp_security/
│   └── reports/
├── scripts/
│   ├── ingest.py
│   ├── benchmark_vllm.sh
│   ├── run_eval.py
│   └── seed_synthetic_data.py
└── tests/
    ├── unit/
    ├── integration/
    ├── e2e/
    └── security/
```

## 25. Environment Variables

Provide `.env.example` with placeholders only:

```dotenv
APP_ENV=development
APP_SECRET_KEY=replace-me
DATABASE_URL=postgresql+psycopg://devguard:replace-me@postgres:5432/devguard
QDRANT_URL=http://qdrant:6333
VLLM_BASE_URL=http://vllm:8000/v1
VLLM_API_KEY=replace-me
MODEL_ID=replace-with-approved-model-id
EMBEDDING_MODEL_ID=replace-with-embedding-model
RERANKER_MODEL_ID=replace-with-reranker-model
LANGFUSE_PUBLIC_KEY=replace-me
LANGFUSE_SECRET_KEY=replace-me
LANGFUSE_HOST=https://cloud.langfuse.com
MCP_AUTH_SECRET=replace-me
MEMORY_TTL_DAYS=90
CHECKPOINT_TTL_DAYS=30
```

Use the appropriate Langfuse host for the selected deployment. If Langfuse Cloud is used, send synthetic/sanitized traces only. For sensitive organizational data, deploy Langfuse in an approved self-hosted environment.

## 26. RunPod Deployment Plan

### 26.1 Deployment topology

- Deploy one GPU Pod for vLLM.
- Deploy CPU services (FastAPI, LangGraph worker, MCP, Qdrant, PostgreSQL) with a documented Compose or separate CPU Pod topology.
- Mount a persistent/network volume for model cache and project artifacts. Database persistence should be backed up independently.
- Expose only required ingress; keep internal services private or behind authenticated reverse proxy.
- Pin container image digests/tags and model revisions.

RunPod's deployment guidance recommends attaching persistent storage for model weights so they survive Pod lifecycle changes. Confirm storage type, data-center compatibility, and persistence semantics for the chosen RunPod plan before deployment. Reference: https://www.runpod.io/articles/guides/deploy-vllm-runpod-docker

### 26.2 Fresh deployment procedure

1. Create a RunPod account/project and select a data center with the intended GPU and storage availability.
2. Create/attach persistent storage for model cache and artifacts.
3. Deploy the GPU Pod using the pinned vLLM image and configured model.
4. Inject model access token (if required) and `VLLM_API_KEY` as secrets.
5. Start vLLM and verify `/health`, `/v1/models`, and an authenticated test completion.
6. Deploy PostgreSQL and Qdrant with persistent volumes/backups.
7. Apply database migrations.
8. Deploy FastAPI, LangGraph worker, and MCP server on private networking.
9. Configure Langfuse credentials and verify trace ingestion.
10. Ingest approved knowledge sources and run index validation.
11. Seed synthetic fixtures and run unit/integration tests.
12. Run the golden evaluation suite and inspect release gates.
13. Run benchmark suite and save the report artifact.
14. Trigger the configured Langfuse alert under controlled conditions and capture evidence.
15. Save a deployment manifest containing GPU SKU, driver/CUDA details, image digests, model revision, quantization, context length, and dependency lockfile hash.

### 26.3 Reproducibility requirements

README must document:
- exact RunPod GPU SKU and region/data center used;
- container images and versions;
- model name, model revision, quantization, and vLLM version;
- environment variables and which are secrets;
- volume mount points and backup instructions;
- one-command local startup if available;
- fresh RunPod deployment commands/steps;
- knowledge ingestion command;
- evaluation and benchmark commands;
- expected health-check results;
- teardown steps to stop GPU usage and avoid unnecessary spend.

## 27. Implementation Plan and Milestones

| Phase | Deliverable | Exit criteria |
|---|---|---|
| P0 — Scope and source inventory | Approved use case, source list, SOP outline, threat boundaries | No production connectivity; data owners/source permissions identified |
| P1 — Infrastructure | RunPod GPU, vLLM, PostgreSQL, Qdrant, Langfuse connectivity | Health checks pass; baseline model responds |
| P2 — Knowledge pipeline | Ingestion, chunking, metadata, hybrid search | Retrieval test set and citations work |
| P3 — Reranking and eval | Cross-encoder, golden retrieval set, metrics report | Recall/ranking metrics recorded |
| P4 — Deterministic MCP tools | Search, payload validator, signature validator, code lookup | Tool tests and schema validation pass |
| P5 — LangGraph agent | Routing, state, tool invocation, structured responses | Four scripted flows pass |
| P6 — Checkpoint and HITL | Postgres checkpointer, interrupt, approval UI/API, idempotency | Crash/resume test passes; no duplicate action |
| P7 — MCP hardening/go-live | AuthN/Z, input validation, audit, security tests | P7 checklist evidence complete |
| P8 — Memory and observability | Memory policies, Langfuse traces, score and alert | Save/update/delete tests and alert firing evidence |
| P9 — Final evaluation and packaging | Final README, report, screenshots, benchmark data | All P0 release gates pass |

## 28. Definition of Done

The capstone is complete when:

- [ ] vLLM serves the selected open-weight model on the RunPod GPU.
- [ ] Hybrid retrieval and reranking are operational.
- [ ] Answers contain resolvable source citations.
- [ ] LangGraph uses durable checkpointing and can resume after interruption/crash.
- [ ] At least three MCP tools are available and secured.
- [ ] High-risk action requires a human approval interrupt.
- [ ] At least one SOP-derived skill is included and versioned.
- [ ] Long-term memory save/update/delete behavior is tested.
- [ ] Four agent scenarios produce recorded results.
- [ ] Crash/resume test proves continuity and no duplicate side effect.
- [ ] MCP security tests show zero unauthorized side effects.
- [ ] Evaluation gate produces PASS/FAIL with metrics and dataset version.
- [ ] Langfuse dashboard and a firing alert are captured.
- [ ] vLLM TTFT/TPOT and retrieval-before/after-reranking results are recorded.
- [ ] README supports reproducible deployment from a fresh RunPod environment.
- [ ] Final report is no more than five pages, with screenshots and actual measurements.

---

# Appendix A — SOP Skill Draft

**Skill name:** `qris-integration-troubleshooting`  
**Purpose:** Guide the agent through a controlled, evidence-based QRIS integration diagnosis.  
**Authority:** A skill is an operational procedure, not a source of truth for protocol requirements. Exact technical rules must still be retrieved from approved specifications.

## Required diagnostic sequence

1. Identify API family, endpoint, environment, and specification version.
2. Sanitize user input and reject/ redact secrets.
3. Classify symptom: authentication/signature, timestamp, payload/schema, response code, or sequence/state.
4. Retrieve the relevant specification and internal SOP sections.
5. Confirm source version and check for conflicts.
6. Run deterministic validator using a named test fixture when possible.
7. Separate confirmed findings from hypotheses.
8. Provide remediation steps with citations.
9. Escalate if evidence is missing, sources conflict, or validators return inconclusive.
10. Require human approval for ticket creation or any write action.
11. Record only sanitized, verified knowledge under the memory policy.

## Escalation criteria

Escalate when a source is missing or contradictory, a validator cannot reproduce the issue, the issue appears environment-specific, production-only evidence is required, or a request would require a production action. The agent must not request production credentials to resolve the issue.

---

# Appendix B — Final Capstone Evidence Checklist

| Required evidence | Artifact to capture |
|---|---|
| Model serving | RunPod GPU details, vLLM health/models response, benchmark output |
| TTFT and TPOT | Benchmark output with hardware/model/workload metadata |
| Hybrid RAG and reranking | Before/after retrieval metric report and example ranked results |
| Citations | Example answer with source title/version/section/page and resolvable citation IDs |
| Four agent runs | Four Langfuse traces and expected-vs-actual result table |
| Crash/resume | Checkpoint ID/thread ID, restart evidence, resumed trace, one ticket record |
| MCP security | Attack test matrix, request/response outcomes, audit events |
| Human approval | Pending approval UI/API, approval/rejection outcome, audit log |
| Skill | SOP-derived `SKILL.md` and version history |
| Memory | Save/update/delete test cases and redaction test |
| Eval gate | PASS/FAIL summary with dataset version and metric values |
| Langfuse | Dashboard screenshot and alert state/notification proving alert fired |
| Reproducibility | README, container/image versions, GPU SKU, configuration manifest |

---

# Appendix C — Reference Documentation

The following official or primary technical references inform the implementation. Confirm the precise versions again when pinning dependencies because these projects evolve.

1. vLLM OpenAI-compatible server and security notes: https://docs.vllm.ai/en/stable/serving/online_serving/openai_compatible_server/
2. vLLM serving benchmark CLI (TTFT/TPOT): https://docs.vllm.ai/en/latest/benchmarking/cli/
3. LangGraph checkpointing and fault-tolerant execution: https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/checkpointers.mdx
4. LangGraph interrupts and human-in-the-loop: https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/interrupts.mdx
5. Qdrant hybrid and multi-stage queries: https://qdrant.tech/documentation/hybrid-queries/
6. MCP security best practices: https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/docs/2026-07-28/tutorials/security/security_best_practices.mdx
7. MCP authorization/security considerations: https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/docs/2026-07-28/basic/authorization/security-considerations.mdx
8. Langfuse alerts: https://langfuse.com/docs/observability/features/alerts
9. Langfuse observability overview: https://langfuse.com/docs/observability/overview
10. RunPod vLLM deployment guide: https://www.runpod.io/articles/guides/deploy-vllm-runpod-docker

