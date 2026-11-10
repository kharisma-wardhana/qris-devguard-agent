# Plan: Four deterministic, read-only MCP tools answer search, payload, signature and response-code questions with machine-checkable results
*Author: Kharisma Wardhana. Stage: Build. Written: 2026-11-10. Via /aspec:make-it-simple-v2.*
*Implements: docs/p4-deterministic-mcp-tools/spec.md*

## Affected files

New: application code
- `app/validators/__init__.py`: package.
- `app/validators/payload.py`: JSON Schema validation, error codes and JSON pointers, input left unchanged.
- `app/validators/rules.py`: named, versioned business rules with source references.
- `app/validators/canonical.py`: the two canonicalization profiles and the named-mistake variants.
- `app/validators/signature.py`: verify (RSA public key / constant-time HMAC), diagnosis, `INCONCLUSIVE` paths.
- `app/validators/keys.py`: generate and load the test keystore (RSA-2048, 64-byte HMAC) by fixture ID.
- `app/validators/response_codes.py`: table loading, exact lookup, suggestions, SNAP PDF row merging.
- `app/validators/fixtures.py`: loads the fixture index and runs every case. Shared by the eval CLI and tests.
- `app/mcp_server/__init__.py`: package.
- `app/mcp_server/config.py`: loads `configs/mcp.yaml` and the role matrix from `configs/security.yaml`.
- `app/mcp_server/errors.py`: §18 error envelope and the safe codes.
- `app/mcp_server/models.py`: strict Pydantic argument and result models for the four tools.
- `app/mcp_server/auth.py`: HS256 `TokenVerifier`, token minting, role check.
- `app/mcp_server/tools.py`: the four handlers, which adapt arguments to `app/validators` and `app/rag`. The search post-filter on API family and version lives here too.
- `app/mcp_server/server.py`: `MCPServer` build, Streamable HTTP app, body-size middleware, loopback and DNS-rebinding settings, one-line call log.

New: scripts
- `scripts/mcp_server.py`: runs the server on loopback.
- `scripts/mint_dev_token.py`: prints a short-lived token for a `sub` and role.
- `scripts/gen_test_keys.py`: writes test keys into the git-ignored key directory (or `--out`).
- `scripts/extract_response_codes.py`: local SNAP PDF → git-ignored `knowledge_base/sources/derived/snap-response-codes-1.0.2.json`, after a checksum check.
- `scripts/eval_validators.py`: fixture report, with exit status tied to `validator_fixture_accuracy`.

New: config and committed synthetic data
- `configs/mcp.yaml`: server, token, limits, search switch, data paths, API family → document map.
- `knowledge_base/fixtures/fixtures.yaml`: fixture index (manifest `local_path`).
- `knowledge_base/fixtures/schemas/access-token-b2b/1.0.2.json`: access-token schema.
- `knowledge_base/fixtures/schemas/qr-mpm-generate/0.1.0.json`: Generate QR schema.
- `knowledge_base/fixtures/schemas/qr-mpm-query/0.1.0.json`: Query Payment schema.
- `knowledge_base/fixtures/payloads/access-token-b2b.yaml`: payload cases for the access-token endpoint.
- `knowledge_base/fixtures/payloads/qr-mpm-generate.yaml`: payload cases for Generate QR.
- `knowledge_base/fixtures/payloads/qr-mpm-query.yaml`: payload cases for Query Payment.
- `knowledge_base/fixtures/signatures/cases.yaml`: signature cases (components + candidate recipe, no signature values).
- `knowledge_base/fixtures/signatures/kat.yaml`: known-answer string-to-sign hashes.
- `knowledge_base/fixtures/response_codes/synthetic-qris-mpm-0.1.0.yaml`: synthetic code table.
- `knowledge_base/fixtures/response_codes/cases.yaml`: lookup cases.

New: tests
- `tests/unit/test_validators_payload.py`
- `tests/unit/test_validators_signature.py`
- `tests/unit/test_validators_response_codes.py`
- `tests/unit/test_validators_fixtures.py`
- `tests/unit/test_mcp_auth.py`
- `tests/unit/test_mcp_tools.py`
- `tests/integration/test_validators_cli.py`: `gen_test_keys`, `extract_response_codes`, `eval_validators` as subprocesses.
- `tests/integration/test_mcp_server.py`: real server subprocess and the SDK Streamable HTTP client.

New: docs
- `docs/p4-deterministic-mcp-tools/validator-report.md`: measured fixture results and the extracted-table count.

Edited
- `pyproject.toml` / `uv.lock`: `mcp==2.3.0`, `pyjwt==2.15.1`, `cryptography==50.0.2` as direct pins.
- `knowledge_base/manifests/sources.yaml`: `synthetic-test-fixtures` completed and approved.
- `app/rag/manifest.py`: skips `kind: fixture_set` with an explicit reason (one check).
- `tests/unit/test_rag_manifest.py`: regression test for that skip.
- `tests/unit/test_check_sources.py`: the real manifest with the approved fixture entry still validates.
- `tests/integration/test_check_sources_cli.py`: its pinned counts change by design, from "4 approved" to "5 approved", and `synthetic-test-fixtures` moves from `candidate` to `approved`. This was found at step 5 and added to the plan before the edit.
- `tests/unit/test_repo_hygiene.py`: no tracked key file, PEM block or `knowledge_base/sources/derived/` file.
- `.gitignore`: checked. `knowledge_base/fixtures/signatures/keys/` and `knowledge_base/sources/*` already cover both generated areas, so it changes only if a check proves otherwise.
- `docs/threat-model.md`: T1, T2, T7, T10 and T14 gain their P4 evidence. T16 gets the derived table. T19 gets the new dependencies.
- `docs/tech.md`, `docs/structure.md`, `README.md`: MCP SDK and protocol version (TRD §13.1), new packages, commands.
- `docs/index.md`: links the plan, tasks and validator report.
- `CLAUDE.md`: commands, a test-scope row, any common mistake learned (see Agent configuration changes).

Not changed: `app/rag/search.py`, `app/rag/rerank.py`, `configs/security.yaml`, `configs/retrieval.yaml`, `scripts/init_env.py` (`MCP_AUTH_SECRET=replace-me` is already in `.env.example`), and every Qdrant collection setting.

## Work sequence

1. **Dependencies and config.** Pin the three packages and run `uv lock`. Add `configs/mcp.yaml`, `app/mcp_server/config.py` and both package `__init__`s. (spec AC #17)
2. **Payload validator.** Write the three schemas, `rules.py`, `payload.py` and the three payload case files (≥4 positive and ≥8 negative each). (AC #6, #7)
3. **Canonicalization, keys and signature validator.** Write `canonical.py`, `keys.py`, `signature.py`, `scripts/gen_test_keys.py`, `signatures/cases.yaml` and `kat.yaml`. (AC #8, #9, #10)
4. **Response-code lookup and extraction.** Write `response_codes.py`, the synthetic table, the lookup cases and `scripts/extract_response_codes.py`. (AC #11, #12)
5. **Fixture index, manifest and the ingest skip.** Write `fixtures.yaml`, update the `sources.yaml` entry, add the `manifest.py` skip and the hygiene test. (AC #16)
6. **Fixture eval CLI.** Write `app/validators/fixtures.py` and `scripts/eval_validators.py`. (AC #14)
7. **MCP layer.** Write `errors.py`, `models.py`, `auth.py`, `tools.py` and `server.py`. (AC #1–#5, #13)
8. **Entry points.** Write `scripts/mcp_server.py`, `scripts/mint_dev_token.py` and the end-to-end server test. (AC #1, #2, #3, #15)
9. **Local measured run.** Extract the real SNAP table, generate keys, run `eval_validators.py`, smoke-test the server with a minted token, and write `validator-report.md` with the measured numbers. (AC #12, #14)
10. **Threat model and steering docs.** Update `threat-model.md`, `tech.md`, `structure.md` and `README.md` with the SDK and protocol versions. (spec Flagged concerns, AC #16)
11. **`CLAUDE.md` merge.** Shown as a diff, written only after `approve write`. (Agent configuration)

Steps 2, 3 and 4 have no dependency on each other and touch no shared file, so they could run in parallel worktrees. With a one-day deadline and small modules, I'll run them sequentially in this session unless you ask for worktrees.

## Test coverage

Matched rows from `test-scope-by-stage.md`:
- **backend service / script entry point:** integration test at the entry point;
- **roles/permissions:** authentication and authorization integration tests;
- **existing project:** regression tests for changed behaviour;
- **always:** the agent-config eval.

Not matched: UI/e2e (no screen) and database (P4 touches no PostgreSQL schema or query).

- **Step 1:** unit tests in `test_mcp_tools.py` check that the config loads, the role matrix is read from `security.yaml`, and `rerank` defaults to `false`.
- **Step 2:** unit tests run every payload case through `payload.py`, each business rule broken and satisfied, and the hash check that the input is unchanged.
- **Step 3:** unit tests cover the KAT hashes, the RFC 4231 HMAC-SHA512 vectors, every signature case, the diagnostic code per named mistake, and that no response field holds the string-to-sign, a key or a signature. Keys are generated into `tmp_path`. **Integration:** `scripts/gen_test_keys.py --out <tmp>` as a subprocess writes the index and prints no key material.
- **Step 4:** unit tests cover lookup `FOUND`/`NOT_FOUND`/`TABLE_UNAVAILABLE`, suggestion labelling, the 300-character cap, and continuation-row merging on synthetic rows. **Integration:** `scripts/extract_response_codes.py` as a subprocess exits non-zero with a one-line error for a missing PDF and for a checksum mismatch (a temp copy of the manifest).
- **Step 5:** **regression** tests show `check_source` skips the `fixture_set` entry and still selects the four document sources (`test_rag_manifest.py`), `check_sources.py` passes on the real manifest (`test_check_sources.py`, and `test_check_sources_cli.py` with its approved count updated from 4 to 5), and the hygiene test finds no tracked key, PEM or derived table.
- **Step 6:** unit tests show the fixture runner returns 100% on the committed set and reports a failure for a wrong expectation. **Integration:** `scripts/eval_validators.py` as a subprocess exits 0 and writes its JSON report, and exits 1 on a temp fixture index with one wrong expectation.
- **Step 7:** unit tests cover:
  - token verification (valid, expired, wrong secret, `alg` `none`/HS512, wrong `aud`, unknown role, TTL over 60 min refused at mint);
  - the role matrix for every role × tool;
  - argument models rejecting extra fields, types, lengths and injection strings;
  - the error envelope with no secret-shaped value or stack trace;
  - `search_qris_spec` on an in-memory Qdrant with the fake embedder (citations, caps, post-filter, `SEARCH_UNAVAILABLE`, reranker not built when off).
- **Step 8:** **auth integration:**
  - **authentication:** no token, a bad token and an expired token get 401 from the real server subprocess;
  - **authorization:** `developer` can call `validate_test_signature`, and `kb_maintainer` gets `FORBIDDEN`.

  **Integration at the entry point:** the server subprocess on a free loopback port, `tools/list` returns exactly four tools, and one successful call of each validator tool runs through the SDK Streamable HTTP client. `scripts/mint_dev_token.py` as a subprocess prints one token and refuses an unknown role.
- **Step 9:** no new test type. It runs steps 2–8's tests plus the real local commands, and the output is pasted into `validator-report.md`.
- **Step 10:** existing `test_repo_hygiene.py` (no IPv4-shaped strings in docs) and `test_licence_hygiene.py`.
- **Step 11:** the agent-config eval (`.claude/hooks/eval.sh` and the existing `agent-config-eval` job) is run in Phase 4.
- **Every step:** the full `uv run pytest` stays green, and existing tests are not edited except the three regression files named above.

## Agent configuration changes

- **`CLAUDE.md` (step 11, own diff, own `approve write`):**
  - `## Commands` gains `gen_test_keys`, `extract_response_codes`, `eval_validators`, `mcp_server` and `mint_dev_token`.
  - `## Test scope` gains one row (append-only): roles/permissions get authentication and authorization integration tests against the real server subprocess.
  - `## Common mistakes` gains only lines actually learned during the build (append-only).
  - `## Project status` changes "No application code exists yet" only if it is still stale. It is project-wide, so that would be flagged rather than rewritten silently.
- **Subagents:** none. `.claude/agents/` doesn't exist, and P4 has no recurring task that needs one.
- **Hooks:** existing `deploy-gate.sh` and the deny-list are verified in Phase 4. The deny-list already blocks reading `knowledge_base/fixtures/signatures/keys/**`.
  - A test-file guard (recipe 3) is proposed in Phase 4 only if fix tasks are ahead, with its own diff and `approve write`.
- **CI:** no change to `ci.yml`. The new tests run in the existing `test` job with no PDFs, models or keys committed. `agent-config-eval` is verified present, not re-added.

## Rollback

Revert the PR. P4 adds no database schema, no Qdrant change and no persisted state. Locally, delete `knowledge_base/fixtures/signatures/keys/` and `knowledge_base/sources/derived/`. Both are git-ignored and regenerated by the scripts.
