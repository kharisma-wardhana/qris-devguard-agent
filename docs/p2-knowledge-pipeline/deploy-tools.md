# Deploy, status and rollback: p2-knowledge-pipeline
*Author: Kharisma Wardhana. Stage: Deploy. Written: 2026-11-09. Via /aspec:make-it-simple-v2.*

These tools are documented only. Nothing here is wired to an MCP server or a credential. Everything runs on the developer machine, against local Qdrant.

## This feature

| Action | How | Who |
|---|---|---|
| Deploy | `uv run python scripts/ingest.py`. It writes the `qris_kb_v1` collection in local Qdrant, and re-runs skip documents whose checksum and pipeline fingerprint are unchanged | Developer |
| Status | `uv run python scripts/search.py "<query>"`; the latest `evals/reports/ingest-*.json` (per-document status, chunk counts, truncation); `uv run python scripts/eval_retrieval.py` for retrieval quality | Developer |
| Rollback (data) | Delete the collection (`DELETE http://127.0.0.1:6333/collections/qris_kb_v1`), then re-ingest at the previous commit. Source files and the manifest are unaffected | Developer |
| Rollback (code) | `git revert` the merge or the step commits | Human (repo owner) |
| Remove model | Delete `~/.cache/huggingface/hub/models--BAAI--bge-m3` | Developer |

## Scoped MCP tools

The deploy tools proposed in `docs/scope-and-source-inventory/deploy-tools.md` and `docs/p1-infrastructure/deploy-tools.md` still apply. P2 adds one:

| Tool | Does | Narrowest scope needed | Approval |
|---|---|---|---|
| `kb_status` | Returns, per ingested document: ID, version, chunk count, source checksum and pipeline fingerprint. Never chunk text | Read-only access to the local Qdrant collection; no credential (Qdrant is loopback-only, P1) | Not required (read-only, no arguments) |

`kb_status` takes no arguments: no URL, collection name, query or path (TRD §13.3). The product's search tool, `search_qris_spec`, is a P4 deliverable, not a deploy tool.
