# Deploy, status and rollback: p4-deterministic-mcp-tools
*Author: Kharisma Wardhana. Stage: Deploy. Written: 2026-11-10. Via /aspec:make-it-simple-v2.*

These tools are documented only. Nothing here is wired to an MCP server or a credential beyond the local, loopback-only P4 server itself. Everything runs on the developer machine.

## This feature

The P4 server is read-only and stores nothing. "Deploying" it means the keys, the derived table and the secret are in place, and the process is running on loopback.

| Action | How | Who |
|---|---|---|
| Deploy (setup) | `uv run python scripts/init_env.py` (only if `.env` is missing; it generates `MCP_AUTH_SECRET`), then `uv run python scripts/gen_test_keys.py` and `uv run python scripts/extract_response_codes.py` (needs the local SNAP PDF; checksum-checked). For real search: `uv sync --extra embed`, `podman compose up -d qdrant` and the P2 collection | Developer |
| Run | `uv run python scripts/mcp_server.py` serves `http://127.0.0.1:8765/mcp`. A client gets a token from `uv run python scripts/mint_dev_token.py --sub <id> --role <role>` (60 min at most) | Developer |
| Status | `uv run python scripts/eval_validators.py` must print `PASS` (100%). A request without a token must get HTTP 401. A minted `developer` token lists exactly four tools. The server log line per call shows the outcome and correlation ID | Developer |
| Rotate the secret | Change `MCP_AUTH_SECRET` in `.env` and restart the server. Every token minted before becomes invalid at once | Developer |
| Rollback (code) | `git revert` the merge or the step commits. P2/P3 search and eval do not depend on P4 | Human (repo owner) |
| Rollback (data) | Nothing persisted. Optionally delete the git-ignored `knowledge_base/fixtures/signatures/keys/` and `knowledge_base/sources/derived/`; the scripts regenerate them | Developer |

## Scoped MCP tools

The deploy/status/rollback tools proposed by earlier features still apply (`docs/scope-and-source-inventory/deploy-tools.md`, `docs/p1-infrastructure/deploy-tools.md`, `docs/p2-knowledge-pipeline/deploy-tools.md`). P4 proposes one more. It is **documented, not wired**:

| Tool | Scope it would need | Never |
|---|---|---|
| `validators_status` (read-only) | Run `scripts/eval_validators.py` against the committed fixture set and an ephemeral key directory; return per-validator counts, the gate result and the fixture-set version | Return keys, signatures or payload values; read `.env`; reach any host |

The four product tools (`search_qris_spec`, `validate_request_payload`, `validate_test_signature`, `lookup_response_code`) are the feature itself, not operations tools. No deploy, status or rollback action is exposed through them.
