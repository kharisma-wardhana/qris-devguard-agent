# Deploy, status and rollback: p3-reranking-eval
*Author: Kharisma Wardhana. Stage: Deploy. Written: 2026-11-09. Via /aspec:make-it-simple-v2.*

These tools are documented only. Nothing here is wired to an MCP server or a credential. Everything runs on the developer machine, against local Qdrant.

## This feature

Reranking runs at query time and stores nothing. "Deploying" it means only that the model is cached and the config is in place.

| Action | How | Who |
|---|---|---|
| Deploy (setup) | `uv sync --extra embed`, then `uv run python scripts/fetch_models.py`, which fetches the pinned reranker (`model.safetensors` only) into the HF cache. The `rerank` block in `configs/retrieval.yaml` sets the model, revision, `max_length`, candidates and `top_n`. No re-ingestion: the `rerank` block is outside the pipeline fingerprint | Developer |
| Status | `uv run python scripts/search.py "<query>" --rerank` shows the fused and reranked ranks. `uv run python scripts/eval_retrieval.py` prints the before/after table and latency; on CPU this takes hours, so use `--no-rerank` for the P2 table alone | Developer |
| Rollback (code) | `git revert` the merge or the step commits. P2 search is unchanged without `--rerank` | Human (repo owner) |
| Rollback (data) | Nothing to delete. Qdrant is unchanged. The golden set reverts with its commit, and the original 30 entries are untouched either way | — |
| Remove model | Delete `~/.cache/huggingface/hub/models--BAAI--bge-reranker-v2-m3` | Developer |

## Scoped MCP tools

The tools proposed in `docs/scope-and-source-inventory/deploy-tools.md`, `docs/p1-infrastructure/deploy-tools.md` and `docs/p2-knowledge-pipeline/deploy-tools.md` still apply. **P3 adds none.**
- The reranker becomes an internal step of P4's read-only `search_qris_spec`. It is not a separate tool.
- `kb_status` (P2) needs no change, because reranking adds nothing to the collection.
