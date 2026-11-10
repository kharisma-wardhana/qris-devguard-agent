# Spec: Four deterministic, read-only MCP tools answer search, payload, signature and response-code questions with machine-checkable results
*Author: Kharisma Wardhana. Stage: Design. Written: 2026-11-10. Via /aspec:make-it-simple-v2.*
*Implements: docs/p4-deterministic-mcp-tools/intent.md*

## Behaviour

**The server.** One MCP server runs locally on `127.0.0.1`. It is reached over Streamable HTTP and needs a bearer token.
- A token is a short-lived signed credential that names a user (`sub`) and a role. A developer gets one from a local script.
- Without a valid token, every request is refused with HTTP 401 before any tool runs.
- With a valid token, the server lists exactly four tools.
- A role that the `configs/security.yaml` matrix doesn't allow for a tool is refused with a structured `FORBIDDEN` error. For example, `kb_maintainer` can't call `validate_test_signature`.
- Every argument is checked by a strict model (types, enums, lengths, ID patterns, no extra fields) before the tool's code runs. A bad argument returns `INVALID_ARGUMENT` with the offending field names, never the values.
- Every error uses the §18 envelope (`code`, `message`, `correlation_id`, optional `field_errors`) and never carries a stack trace, an environment value, a key or a signature.

**`search_qris_spec`** takes a question, an optional API family, an optional document version and `top_k` (1–10, default 5). It returns ranked passages with the same citation metadata, 300-character excerpt cap, SYNTHETIC label and untrusted-text marker as `scripts/search.py`.
- Reranking is off unless `configs/mcp.yaml` turns it on. The result says whether it was used.
- If Qdrant or the embedding model isn't available, it returns `SEARCH_UNAVAILABLE`. It never returns an empty "no results" in that case.

**`validate_request_payload`** takes an API family, an endpoint ID (`access-token-b2b`, `qr-mpm-generate` or `qr-mpm-query`), a spec version and a sanitized payload `{headers, body}`.
- It answers `valid: true/false` with a list of errors and warnings, plus the schema and rule-set version it used.
- Each error has a machine-readable code, a JSON pointer to the field (for example `/body/amount/value`) and the source section the rule comes from. It never echoes the field's value.
- The payload is never changed.
- Unknown endpoint/version pairs are refused as `INVALID_ARGUMENT`. They are not validated against a "closest" schema.

**`validate_test_signature`** takes an algorithm, a test-key fixture ID, a canonicalization profile and a reference to a committed signature test case.
- It rebuilds the string-to-sign with the named, versioned profile and checks the case's candidate signature against the test key:
  - SHA256withRSA verified with the public key;
  - HMAC_SHA512 compared in constant time.
- It answers `VALID`, `INVALID` or `INCONCLUSIVE`, with a diagnostic code. When a signature is `INVALID`, the validator tries a fixed list of named, common mistakes and reports which one reproduces the candidate, if any. Examples: body not minified, keys reordered, host included in the path, wrong separator, hex instead of base64, wrong key.
- It returns the SHA-256 of the string-to-sign for traceability. It never returns the string-to-sign itself, a key or any signature.
- Missing keys, an algorithm that doesn't fit the profile, or a test case missing a component gives `INCONCLUSIVE` with the reason. It never guesses.

**`lookup_response_code`** takes a 7-digit code, an API family and a spec version.
- It splits the code into HTTP status, service code and case code, and looks for an exact entry.
- It answers `FOUND` with category, message, description (capped at 300 characters for reference-only sources) and a citation, or `NOT_FOUND`.
- When the table for that family and version isn't present on this machine, it answers `TABLE_UNAVAILABLE`. That happens for the real SNAP table in CI.
- Up to three "suggestions" (same HTTP status) may be listed, each labelled as a suggestion, not a match.
- A synthetic table's answers carry the SYNTHETIC label.

**The fixture report.** `scripts/eval_validators.py` runs every committed payload, signature and response-code fixture. It prints a pass/fail table per validator and writes a JSON report to `evals/reports/`. It exits non-zero unless 100% of fixtures return their expected result (`validator_fixture_accuracy` in `configs/eval_thresholds.yaml`).

## Architecture and the reason for it

Conforms to `docs/tech.md` and `docs/structure.md` (the TRD §24 layout). The only departure is noted under the HTTP layer below.

- **MCP Python SDK `mcp==2.3.0`** (MIT), using `MCPServer` from `mcp.server.mcpserver`. The latest negotiable protocol is `2026-07-28`; the handshake versions go back to `2024-11-05`. Both are recorded in the README (TRD §13.1). *Reason:* it is the current SDK, and it ships Streamable HTTP and a `TokenVerifier` hook, so no OAuth server is needed.
- **Auth: HS256 JWT signed with `MCP_AUTH_SECRET`**, verified by a `TokenVerifier` through `PyJWT` (pinned). Claims: `sub`, `role`, `aud = qris-devguard-mcp`, `iss = qris-devguard-local`, `iat`, `exp`. `exp` is required, and the maximum TTL is 60 minutes.
  - Only `HS256` is accepted (no `none`, no algorithm switching).
  - `role` must be one of the four P0 roles.
  - `scripts/mint_dev_token.py` mints a token for a given `sub` and role. `scripts/init_env.py` generates `MCP_AUTH_SECRET` if it is missing.
  - *Reason:* the identity and role reach every call without an identity provider, which P4 doesn't need. P7 can swap the verifier without touching the tools.
- **Authorization** happens inside each tool via the SDK's `get_access_token()`. The role is checked against `configs/security.yaml` `tools.<name>.roles`, which stays the single source of truth.
- **HTTP layer:** Starlette/uvicorn from the SDK, bound to `127.0.0.1` only, with the SDK's DNS-rebinding protection set to that host and port.
  - A body-size middleware refuses requests over 64 KiB, double the 32 KiB payload limit, which allows for the JSON-RPC wrapper.
  - *Departure:* the TRD names FastAPI for the app API. The MCP server doesn't use it, because the SDK brings its own ASGI app. The `/api/v1` API arrives in P5.
- **`app/validators/`** holds pure functions with no I/O beyond reading committed schemas, tables and the key directory: `payload.py`, `rules.py`, `canonical.py`, `signature.py` and `response_codes.py`.
  - The MCP layer (`app/mcp_server/`) only adapts arguments and results. The tools can then be tested without a server, and P5 calls the same functions.
- **JSON Schema draft 2020-12** through the existing `jsonschema==4.26.0` dependency. There is one schema file per endpoint and version.
  - Business rules are separate, named, versioned functions with a source reference. Examples: `validityPeriod` must be after `X-TIMESTAMP`; exactly one of `originalReferenceNo` / `originalPartnerReferenceNo`; `serviceCode` is `"47"`.
  - Timestamps are checked by an explicit pattern for `yyyy-MM-ddTHH:mm:ss.SSS±HH:MM`, not by the library's `date-time` format, which accepts other forms.
  - Header names are matched case-insensitively.
- **Crypto through `cryptography`** (already in the lock at 50.0.2, now a direct, pinned dependency) and the standard library `hmac`/`hashlib`. RSA uses PKCS#1 v1.5 padding with SHA-256, and signatures are base64. These are the canonicalization profiles:

  | Profile | Algorithm | String-to-sign | Source |
  |---|---|---|---|
  | `snap-asymmetric-access-token-v1` | SHA256withRSA | `clientKey + "\|" + X-TIMESTAMP` | `snap-technical-security-standard` 1.0.2 §2.1.5(a) |
  | `synthetic-snap-symmetric-v1` | HMAC_SHA512 | `METHOD:path:accessToken:lower(hex(sha256(minified body))):X-TIMESTAMP` | `synthetic-qris-mpm-service-spec` 0.1.0 §1.4 |

- **Test keys are generated at runtime.** `scripts/gen_test_keys.py` writes RSA-2048 pairs and 64-byte HMAC secrets, one per fixture ID, into the git-ignored `knowledge_base/fixtures/signatures/keys/`. This entry is already in `.gitignore` from P0.
  - Tests generate them into a pytest temp directory.
  - Committed signature cases say *how* the candidate is produced (correct, a named mistake, wrong key, a literal invalid string), never a signature value. Outcomes are therefore deterministic with any freshly generated key.
- **Response-code tables:**
  - The real SNAP table is produced locally by `scripts/extract_response_codes.py` from pages 565–570 of the local `snap-data-technical-spec` PDF (part 6, Response Code). It is written to the git-ignored `knowledge_base/sources/derived/snap-response-codes-1.0.2.json`, with the PDF checksum checked against the manifest first.
  - The synthetic MPM table (service codes 47 and 51) is committed as `knowledge_base/fixtures/response_codes/synthetic-qris-mpm-0.1.0.yaml`. It is transcribed from the synthetic spec §2 and §3.
- **Config** lives in a new `configs/mcp.yaml`: host, port, token audience/issuer/TTL, body limit, `search.rerank` (default `false`), `search.top_k_max`, and the paths to the schemas, rules, tables and key directory. Model and retrieval settings stay in `configs/retrieval.yaml`.
- **Logging** writes one line per tool call: tool, role, `sub`, outcome code, `correlation_id`, duration. It never logs arguments or results. The audit table is P7.

## Data

- **Committed and synthetic**, under `knowledge_base/fixtures/`:
  - JSON Schemas (`schemas/<endpoint>/<version>.json`);
  - payload fixture cases (`payloads/*.yaml`, each with input and expected result);
  - signature cases (`signatures/cases.yaml`), with components and the candidate recipe;
  - the synthetic response-code table.

  All values are synthetic (`SYN-` prefixes). Sensitivity: none.
- **Generated and git-ignored, never committed:**
  - test keys under `knowledge_base/fixtures/signatures/keys/`. Sensitivity: fake but secret-shaped, treated as secrets (T6).
  - the extracted SNAP code table under `knowledge_base/sources/derived/`. Licence: reference-only, never committed (T16).
- **Reports:** `evals/reports/validators-<timestamp>.json` (git-ignored) holds fixture IDs, expected and actual outcomes and codes, with no key or signature material.
- **Manifest:** `synthetic-test-fixtures` is completed: version `0.1.0`, effective `2026-11`, `approved`, `local_path` = `knowledge_base/fixtures/fixtures.yaml` (the fixture index) and checksum. RAG ingestion skips `kind: fixture_set` entries, because fixtures are test data, not retrievable documentation.
- **Nothing is persisted at run time.** The tools are read-only and keep no state between calls. No retention applies.

## Acceptance criteria

1. Without a bearer token, an expired token, a token signed with another secret, an `alg` other than HS256, a wrong `aud`, or an unknown `role`, every MCP request gets HTTP 401 and no tool handler runs.
2. With a valid token, `tools/list` returns exactly `search_qris_spec`, `validate_request_payload`, `validate_test_signature` and `lookup_response_code`. No write, payment, refund, URL, SQL, shell or path tool exists.
3. Every role/tool pair in `configs/security.yaml` is honoured: allowed pairs reach the handler, and every other pair returns `FORBIDDEN` without running it. One test per role covers all four tools.
4. A tool argument with an extra field, a wrong type, an out-of-range `top_k`, a string over its length limit, an identifier outside its pattern (including `../`, `http://`, `'; DROP`), or a payload over 32 KiB returns `INVALID_ARGUMENT` with `field_errors`, and the handler doesn't run.
5. No error response contains a stack trace, an environment value, a key, a signature or a submitted field value. A test sends secret-shaped values in arguments and asserts they are absent from the response and the log line.
6. Each of the three endpoints has at least 4 positive and 8 negative payload fixtures. Every one returns its expected `valid` value, error codes and JSON pointers, covering: missing required field, wrong type, bad enum, over-length, bad timestamp format, bad amount format, unexpected top-level field, and each business rule both broken and satisfied.
7. The payload validator returns the schema version and rule-set version it used, and leaves the input object byte-identical (checked by hashing before and after).
8. Each canonicalization profile has a known-answer test: committed components produce a committed SHA-256 of the string-to-sign. The HMAC-SHA512 primitive passes the RFC 4231 test vectors.
9. Each signature profile has at least 3 `VALID` and 8 `INVALID` cases plus 3 `INCONCLUSIVE` cases, and every one returns its expected result and diagnostic code.
   - `INVALID` covers: body whitespace, non-ASCII body encoding, key reordering, timestamp change, a tampered body field, wrong key, wrong separator, and hex instead of base64.
   - `INCONCLUSIVE` covers: missing key, algorithm/profile mismatch, and a missing component.
10. Each `INVALID` case built from a named mistake reports that mistake's diagnostic code. No signature response contains the string-to-sign, a key or a signature value.
11. `lookup_response_code` returns `FOUND` with the exact entry for every code in the synthetic MPM table, and `NOT_FOUND` for a well-formed code absent from it. Without the extracted SNAP table, it returns `TABLE_UNAVAILABLE` for `snap`, never `NOT_FOUND`. Suggestions are labelled `suggestion`, and a `NOT_FOUND` result never carries a meaning.
12. `scripts/extract_response_codes.py`, run locally on the approved PDF, writes a table whose row count and a spot-checked sample are recorded in `docs/p4-deterministic-mcp-tools/validator-report.md`. It refuses to run if the PDF's checksum differs from the manifest. A unit test covers its row-merging logic on synthetic rows.
13. `search_qris_spec` against an in-memory store with the fake embedder returns passages with the full citation contract and excerpt caps. With the store unavailable, it returns `SEARCH_UNAVAILABLE`. With `search.rerank: false`, it never loads the reranker.
14. `scripts/eval_validators.py` exits 0, prints 100% for every validator, and writes its JSON report. It exits non-zero when any fixture's actual result differs from its expected one (tested with a deliberately wrong fixture in a temp copy).
15. An integration test starts the real server as a subprocess on a free loopback port. It connects with the SDK's Streamable HTTP client and a minted token, and checks criteria 1–3 end to end plus one successful call of each validator tool. The server never binds a non-loopback address.
16. `uv run python scripts/check_sources.py` passes with `synthetic-test-fixtures` approved, and RAG ingestion selection skips it (regression test). `git ls-files` contains no key file, no PEM block and no extracted SNAP table (hygiene test).
17. The existing suite passes unchanged, and CI passes with no PDFs, models, Qdrant or committed key material.

## Flagged concerns

- **Licence (T16): SNAP-derived artefacts.** The committed `access-token-b2b` JSON Schema encodes field names and rules read from a reference-only document. It is a team-written schema with no copied prose, and each rule cites its section. The extracted response-code table stays git-ignored, and its descriptions are capped at 300 characters in answers. *Decides:* Kharisma Wardhana (kb_maintainer), at this gate.
- **SHA256withRSA details not stated on the cited page.** The SNAP page gives the string-to-sign and the algorithm name but not the padding or the signature encoding. The profile assumes PKCS#1 v1.5 and base64, labels that assumption in its docstring and in the validator report, and is versioned so that a correction becomes `-v2`, not a silent change. *Decides:* Kharisma Wardhana.
- **The signature tool checks committed test cases only** (the TRD §13.2 contract: `fixture_id` + `test_payload_ref`). In P4, a developer can't submit their own signature for checking. P5's Journey A will diagnose by mapping the symptom to the closest fixture case and its named mistakes. If a "check my signature" input is wanted, it is a contract change for P5, and the agent state must then carry only a hash (CLAUDE.md). *Decides:* Kharisma Wardhana.
- **Security: one shared HS256 secret.** Anyone holding `MCP_AUTH_SECRET` can mint any role. This is acceptable for a loopback-only sandbox server. P7 revisits it (an asymmetric issuer or a gateway), along with rate limiting, the audit table, TLS and the full §22.5 test set, which stay out of P4. *Decides:* Kharisma Wardhana.
- **Privacy:** `additionalInfo` is free-form and could carry personal data a user pastes in. The tools persist nothing and log no arguments, and the agent-side redaction is P5's `sanitize_input`. *Decides:* none further; recorded for P5.
- **Brand (T17):** synthetic table answers and synthetic fixtures carry the SYNTHETIC label. Checked; no further decision.
- **UX and accessibility:** none found. P4 has no user interface; its only user surface is MCP JSON results and CLI output.

## Out of scope

- `create_support_ticket` and any write, approval or idempotency logic (P6).
- The LangGraph agent, the `/api/v1` FastAPI app, and which tool the agent picks (P5).
- Rate limiting, the persistent audit table, TLS or a gateway, least-privilege DB accounts, and the full §22.5 security test report (P7).
- Validating signatures or payloads a developer submits outside the committed fixture cases (see Flagged concerns).
- Timestamp skew against the server clock. It is not deterministic, so the validator checks only format and ordering rules.
- Endpoints beyond the three in `docs/scope.md` §1.1, and any production key, token or host.
- Running the CPU reranker inside the tool by default, and any GPU use.

## Resolved questions

- **SDK, protocol and identity?** `mcp==2.3.0` (MIT), `MCPServer` over Streamable HTTP. The latest protocol is `2026-07-28` (handshake versions `2024-11-05`…`2025-11-25`). The SDK's `TokenVerifier` hook handles bearer checking without an OAuth server. Identity and role travel as signed claims (`sub`, `role`) in an HS256 JWT minted locally, not as a token-to-role map. A map would need its own secret store, while a signed claim can't be edited by the holder.
- **Which fields and rules go in each schema, and which source backs them?**
  - `access-token-b2b` follows `snap-technical-security-standard` 1.0.2 §2.1.5(a):
    - headers `Content-Type`, `X-TIMESTAMP`, `X-CLIENT-KEY`, `X-SIGNATURE`;
    - body `grantType` = `client_credentials` and optional `additionalInfo`.
  - `qr-mpm-generate` and `qr-mpm-query` follow `synthetic-qris-mpm-service-spec` 0.1.0 §1.3, §2 and §3: the field tables, lengths, `IDR` only and amount format.
  - JSON Schema holds presence, type, enum, pattern, length and additional properties. Separate business rules hold the cross-field checks: `validityPeriod` after `X-TIMESTAMP`, exactly one original reference, `serviceCode` = `"47"`.
- **Canonical formats, profile names, and resolving `test_payload_ref` without a path?** See the profile table above. `test_payload_ref` is a case ID matching `^[a-z0-9][a-z0-9-]{0,63}$`, looked up in the committed `signatures/cases.yaml`. `fixture_id` is looked up the same way in the key directory index. No argument is ever joined into a path.
- **Can the SNAP table be extracted?** Yes. `pdfplumber` reads part 6 on pages 565–570 as tables with columns Category / HTTP Status Code / Service Code / Case Code / Response Message / Description. Continuation rows (empty category) are merged into the row above. Service code is `any` in the generic table. The lookup reports which table answered (`document_id`, version, synthetic flag) in every result.
- **Which libraries?** `jsonschema==4.26.0` (draft 2020-12, already a dependency), `cryptography==50.0.2` (already in the lock, now direct), `PyJWT` and `mcp==2.3.0`. All are MIT or Apache/BSD and pinned exactly in `uv.lock`, and all are covered by the weekly pip-audit.
- **How many fixtures, and where does the report go?** At least 4 positive and 8 negative per endpoint (36+ payload fixtures). At least 3 valid, 8 invalid and 3 inconclusive per signature profile (28+). The response-code cases cover every synthetic code plus not-found and table-unavailable. The report goes to `evals/reports/validators-<timestamp>.json`, summarised with measured numbers in `validator-report.md`. The §22.6 gate is read from `validator_fixture_accuracy`.
- **How much of P7 lands now?** Bearer auth (signed identity and role), the role/tool matrix check, strict argument models, size limits, the safe error envelope, loopback binding and DNS-rebinding protection. Left for P7: rate limiting, the persistent audit table, TLS or a gateway, least-privilege DB credentials, and the full §22.5 report.
