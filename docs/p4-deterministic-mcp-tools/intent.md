# Intent: Four deterministic, read-only MCP tools answer search, payload, signature and response-code questions with machine-checkable results
*Author: Kharisma Wardhana. Stage: Plan. Written: 2026-11-10. Via /aspec:make-it-simple-v2.*

## Problem statement

P1–P3 left infrastructure, a hybrid search with citations, and a measured reranker. Nothing yet can *check* anything, so the PRD's P4 exit criterion, *tool tests and schema validation pass*, isn't met:

- **No validator exists.** The core invariant is that the LLM never decides crypto or payload validity (CLAUDE.md, TRD §14). Without deterministic validators, the P5 agent would have nothing to call for a validation statement and would have to guess or abstain on every signature or payload question.
- **No MCP server exists.** TRD §13.2 defines the tool surface the agent reaches through, and `configs/security.yaml` already holds a role/tool matrix. But no server, no tool schemas and no MCP SDK are in the repo.
- **No fixtures exist.** The release gate "deterministic validators: 100% of positive/negative fixtures return expected result" (TRD §22.6) has no fixture set to measure. The `synthetic-test-fixtures` manifest entry is still `candidate`, marked "Created in Phase P4".
- **Response codes can only be searched, not looked up.** A developer asking what `4017300` means gets ranked passages today, not an exact match or an explicit `NOT_FOUND` (TRD §14.3).

## Proposed outcome

- **An MCP server reachable over Streamable HTTP with bearer authentication.** Every call is bound to an authenticated identity and role, checked against the `configs/security.yaml` matrix. It exposes exactly four read-only tools. Every argument is validated by a strict Pydantic model before the handler runs. Errors are safe and structured, with no stack traces.
- **`search_qris_spec`.** It wraps the P2 hybrid search and returns ranked passages with the existing citation metadata and excerpt caps. Reranking is available but off by default (config switch), because the CPU reranker takes about 160 s per query (P3 report).
- **`validate_request_payload`.** It checks a sanitized payload for `access-token-b2b`, `qr-mpm-generate` and `qr-mpm-query` against versioned JSON Schema files. Business rules are separate, auditable checks. It returns `valid`, machine-readable error codes with JSON pointers, warnings and the schema version. It never alters the payload.
- **`validate_test_signature`.** It verifies test signatures with named, versioned canonicalization functions:
  - SHA256withRSA for `access-token-b2b`;
  - HMAC_SHA512 for the two MPM endpoints.

  It checks against fixture-referenced test keys only and returns valid/invalid/`INCONCLUSIVE` with a diagnostic code. It never returns key or signature material.
- **`lookup_response_code`.** It does an exact match by API family and version against a response-code table, and returns `NOT_FOUND` for unknown codes. Similar codes, if shown, are labelled as suggestions.
  - The real SNAP table is extracted from the local PDF into the git-ignored `knowledge_base/sources/`.
  - CI and tests use a small committed synthetic table that carries the SYNTHETIC label.
- **A positive/negative fixture set** for payloads and signatures, covering whitespace, encoding, field ordering, timestamp and tampering cases. Every fixture returns its expected result in CI. The `synthetic-test-fixtures` manifest entry is completed.
- **Test key material generated at runtime.** RSA pairs and HMAC secrets are generated into a git-ignored directory, or a temp directory in CI. Fixtures refer to them by `fixture_id`, and nothing secret is committed.

## Affected users and systems

- Kharisma Wardhana: reviews the schemas, business rules and fixture expectations, and runs the server locally.
- `app/mcp_server/` (new): server, transport, auth and tool registration.
- `app/validators/` (new): payload validator, signature validator, canonicalization profiles and response-code lookup.
- `app/rag/`: called by `search_qris_spec`, unchanged.
- `configs/`: tool limits, schema versions, the rerank switch and the MCP server settings. `security.yaml` is read and its role matrix is enforced.
- `knowledge_base/`: JSON Schemas, payload fixtures, the synthetic response-code table, and the `synthetic-test-fixtures` manifest entry.
- `.env` / `scripts/init_env.py`: `MCP_AUTH_SECRET`, already named in the threat model.
- `pyproject.toml` / `uv.lock`: MCP Python SDK, JSON Schema validator and crypto library, pinned exactly.
- CI: must pass with no PDFs, no models, no services and no committed key material.
- Downstream: P5 (the agent's `select_tools` / `execute_read_tools`), P6 (adds `create_support_ticket`), P7 (hardening, rate limits, audit and the §22.5 security test set).

## Constraints

- **Deadline:** P4 is done by 2026-11-11. All of P1–P9 must be done by 2026-11-20.
- **No GPU spend.** Everything runs on the local CPU. GPU budget is $19.57 left for P5–P9.
- **Read-only.** No tool in P4 mutates state. `create_support_ticket` is not built in P4. Prohibited actions (payment, refund, production calls…) do not exist as tools.
- **No LLM in any validation decision.** Validators are pure, deterministic code. Unknown or missing context returns `INCONCLUSIVE` / `NOT_FOUND`, never a guess.
- **No secrets in git.** Test keys are generated at runtime outside version control. Signature values and key material never appear in responses, logs or errors. The redaction fields in `configs/security.yaml` apply.
- **Licence:** no ASPI text, and no extracted SNAP response-code table, is committed. The real table stays under the git-ignored `knowledge_base/sources/`, and excerpts stay capped at 300 characters.
- **Synthetic only:** all fixtures, merchant and transaction references are synthetic and labelled as such.
- **Untrusted input:** tools take no URL, SQL, shell or path arguments. Identifiers are allowlisted or regex-bound, and strings have maximum lengths.
- **Network:** the MCP server listens on loopback only, and egress stays within `configs/security.yaml`.
- **Must not break:** P2/P3 search and eval behaviour and all existing tests.

## Open questions

- Which MCP Python SDK version, and which protocol version, does it pin? Does its Streamable HTTP transport support a bearer-token check without a full OAuth server? How is the caller's identity and role carried (signed token claims, or a token-to-role map in config)?
- Exactly which fields, types, formats and lengths go into each of the three JSON Schemas? Which source sections back each one (the SNAP security standard for `access-token-b2b`; the synthetic MPM spec §2 and §3)? Which rules are JSON Schema and which are separate business rules?
- What are the exact canonical string formats for SHA256withRSA (access token) and HMAC_SHA512 (transactional), and what are the profile names and versions? How is the `test_payload_ref` resolved to a fixture without accepting a path?
- Can the SNAP response-code table be extracted reliably from the local PDF with `pdfplumber`? Which columns are kept, and how does the lookup report which table (real or synthetic) answered?
- Which JSON Schema library and draft, and which crypto library (`cryptography`?), fit the existing pinned stack and licences?
- How many fixtures make a meaningful positive/negative set per endpoint and per signature profile? Where does the fixture report go, so that the §22.6 "100% of fixtures" gate can be read from it?
- How much of P7 lands now? Bearer auth and the role check are in. Are rate limiting, audit logging and the full §22.5 security test set left to P7?
