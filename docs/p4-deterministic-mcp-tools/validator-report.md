# Validator report: p4-deterministic-mcp-tools
*Author: Kharisma Wardhana. Stage: Test. Written: 2026-11-10. Via /aspec:make-it-simple-v2.*

**These are measured results** from one local run on the developer machine (Windows, CPU), not targets (CLAUDE.md). Raw report: `evals/reports/validators-2026-11-10T095607.json` (git-ignored; IDs, statuses and codes only).

## Fixture eval (spec AC #14; gate `validator_fixture_accuracy` ≥ 1.00)

`uv run python scripts/eval_validators.py`, fixture set 0.1.0, run after the SNAP table was extracted, so no case was skipped:

```
validator        passed failed skipped  accuracy
payload              51      0       0     1.000
signature            35      0       0     1.000
response_codes       24      0       0     1.000
validator_fixture_accuracy gate (>= 1.00): PASS  report: ...\evals\reports\validators-2026-11-10T095607.json
```

| Validator | Cases | What the cases cover |
|---|---|---|
| Payload | 51 (18 / 18 / 15 per endpoint; 15 positive, 36 negative) | missing field, type, enum, format, length, unexpected field, duplicate header, each business rule broken |
| Signature | 35 (16 RSA, 19 HMAC; 7 VALID, 20 INVALID, 8 INCONCLUSIVE) | every named mistake, hex vs base64, wrong key, tampered timestamp/body/client key, missing key, algorithm/profile/key-type mismatch, missing component, body not JSON |
| Response codes | 24 (14 synthetic found, 4 not found, 1 table unavailable, 5 real SNAP) | exact match, labelled suggestions, no fallback from the MPM table to SNAP |

In CI the 5 real-SNAP cases are **skipped, not passed**, because the table is never committed. The other 105 cases run there with freshly generated keys.

## SNAP response-code extraction (spec AC #12)

`uv run python scripts/extract_response_codes.py`:

```
wrote 61 entries to ...\knowledge_base\sources\derived\snap-response-codes-1.0.2.json (HTTP statuses: 200, 202, 400, 401, 403, 404, 405, 409, 429, 500, 504)
```

- Source: `snap-data-technical-spec` 1.0.2, part 6, pages 565–570. The PDF checksum matched the manifest before parsing.
- 61 entries, no duplicate (HTTP, service, case) keys. Every entry has service code `any`: the table is generic across services, so `4017300` matches HTTP 401, case 00 as `match_type: generic_service_code`.
- Case codes are contiguous within each HTTP status (for example, 403 runs 00–23 and 404 runs 00–19).
- Three continuation rows were merged into the entry above them (pages 566, 569 and 570). The longest (404/18, 601 characters after the merge) was checked at the join point. The sentence runs across the page break, so this is a real page split, not a parsing error. Answers cap descriptions at 300 characters.
- Spot checks through the running server: `4017300` gives `FOUND`, category System, page 565. `4017399` gives `NOT_FOUND` with 3 same-status suggestions, each labelled `suggestion, not a match`.
- The file is git-ignored. `test_repo_hygiene.py` fails if anything under `knowledge_base/sources/derived/` is ever tracked.

## Server smoke test (spec AC #1, #2, #13, #15)

`scripts/mcp_server.py` was run with the real `.env` secret, with local Qdrant up (the P2 collection) and the CPU embedding model. A throwaway client minted a `developer` token in-process; the token was never printed.

```
no token -> 401
protocol: 2025-11-25 server: qris-devguard-mcp
tools: ['search_qris_spec', 'validate_request_payload', 'validate_test_signature', 'lookup_response_code']
search_qris_spec           error=False    29558 ms  [('snap-technical-security-standard', '2.1.5', 1), ('snap-technical-security-standard', '2.1.6#2', 2), ('snap-technical-security-standard', '2.1.5', 3)]
search_qris_spec           error=False      342 ms  [('synthetic-qris-mpm-service-spec', '3.request-fields', 1), ('synthetic-qris-mpm-service-spec', '2.response-codes', 4), ('synthetic-qris-mpm-service-spec', '3.required-headers', 6)]
validate_request_payload   error=False       27 ms  (False, [('SCHEMA_FORMAT', '/headers/X-TIMESTAMP')])
validate_test_signature    error=False      189 ms  ('INVALID', 'SIG_WRONG_SEPARATOR', ['PKCS#1 v1.5 padding and base64 encoding are project assumptions; the cited page does not state them'])
lookup_response_code       error=False        6 ms  ('FOUND', 'System', 565, 0)
lookup_response_code       error=False        5 ms  ('NOT_FOUND', None, None, 3)
```

The server log had one line per call (tool, sub, role, outcome, correlation ID, duration). A search of the log for the submitted values (`SYN-CLIENT`, `grantType`, `X-TIMESTAMP`, `Bearer`, the case ID) found **0** matches.

### What the smoke test shows

- **The first search costs about 30 s** because it loads BGE-M3 on CPU. Later searches took about 0.3 s. Reranking stayed off (`search.rerank: false`); P3 measured it at about 160 s per query on this CPU.
- **The negotiated protocol was `2025-11-25`.** The SDK's `ClientSession` uses the initialize handshake, whose latest version is `2025-11-25`. The server also supports the stateless `2026-07-28` envelope, which a client opts into separately. Both are recorded in the README (TRD §13.1).
- **Filtered search ranks are pool ranks.** With `api_family: qris-mpm` the results came from hybrid ranks 1, 4 and 6 of the 20-candidate pool. The rank field is kept as retrieved, not renumbered, so the agent can see that filtering happened.
- The second query ("duplicate partnerReferenceNo…") ranked `3.request-fields` above `2.response-codes`, where the 409 case lives. That is retrieval quality (P2/P3), not tool behaviour. It is recorded here for P5's `rerank_evidence` and not changed in P4.

## Test suite

`uv run pytest`, gated on pytest's own exit code: **530 passed**, 1 warning. The warning comes from PyJWT, because one test deliberately mints an HS512 token to prove it is refused. The suite includes:
- the real-server subprocess tests: 401 for no, garbage, foreign-secret and expired tokens; foreign `Host` refused; four tools listed; `kb_maintainer` gets `FORBIDDEN` on the signature tool; the listener is loopback only;
- the role × tool matrix: 16 pairs;
- injection strings in six identifier fields;
- secret-shaped values absent from responses and logs.

## Against the spec's acceptance criteria

| AC | Result |
|---|---|
| 1 | 401 for missing, garbage, foreign-secret and expired tokens against the real server. Unit tests add HS512, `alg: none`, wrong aud/iss, unknown or missing role, missing exp, TTL over 60 min, and a bad sub. |
| 2 | Exactly four tools, all annotated read-only and non-destructive. No ticket/payment/URL/SQL/shell/path tool. |
| 3 | 16/16 role × tool pairs behave as the `security.yaml` matrix says. |
| 4 | Extra field, wrong type, range, length, enum, oversized payload and six injection strings (path, link-local URL, SQL, shell, newline, Windows path) all give `INVALID_ARGUMENT` before the handler runs. |
| 5 | No value, trace or env text in any error. The internal-error test returns only `code`, `message` and `correlation_id`. |
| 6 | 51 payload cases, ≥4 positive and ≥8 negative per endpoint, 100%. |
| 7 | Input hash unchanged. The schema version and rules version (0.1.0) are reported. |
| 8 | 3 known-answer hashes (computed independently) and 2 RFC 4231 HMAC-SHA512 vectors pass. |
| 9 | 35 signature cases, ≥3 / 8 / 3 per profile, 100%. |
| 10 | Every named mistake has a case that reports its own code. No string-to-sign, key or signature appears in any result. |
| 11 | All 14 synthetic codes `FOUND`. `NOT_FOUND` carries no meaning. `TABLE_UNAVAILABLE` (not `NOT_FOUND`) when the SNAP table is absent. |
| 12 | 61 entries, recorded above. Refuses a missing PDF and a checksum mismatch (integration tests). Row merging is unit-tested on synthetic rows. |
| 13 | Search returns the citation contract and caps from an in-memory store. `SEARCH_UNAVAILABLE` without a store, with no address leaked. The reranker is never built when off. |
| 14 | Exit 0 at 100%. Exit 1 on a deliberately wrong expectation (integration test). |
| 15 | Real server subprocess plus the SDK Streamable HTTP client: AC 1–3 end to end and one successful call of each validator tool. |
| 16 | `check_sources.py`: `OK: 5 sources, 5 approved`. Ingestion skips `fixture_set`. No tracked key, PEM or derived table. |
| 17 | The 267 tests that existed before P4 still pass. Three existing tests changed where approving the fixture set changes an expected count or set; each is recorded in `plan.md`. |
