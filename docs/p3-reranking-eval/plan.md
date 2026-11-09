# Plan: A cross-encoder reranker reorders the hybrid top-20, and its before/after effect is measured on a harder, reviewed query set
*Author: Kharisma Wardhana. Stage: Build. Written: 2026-11-09. Via /aspec:make-it-simple-v2.*
*Implements: docs/p3-reranking-eval/spec.md*

## Affected files

- `evals/datasets/retrieval_golden.hard-draft.yaml`: new, temporary. The drafted hard queries for your review. It is deleted when the reviewed entries move into the golden set (step 7).
- `configs/retrieval.yaml`: edit. A new `rerank` block. The existing keys are unchanged, so P2's `pipeline_fingerprint` (chunking, sparse and embedding only) is unaffected and nothing re-ingests.
- `app/rag/rerank.py`: new. The `Reranker` interface, `CrossEncoderReranker`, `FakeReranker`, `rerank()` and `select()`.
- `app/rag/search.py`: edit. `Citation` gains an optional `rerank_score` (default `None`). The search behaviour is unchanged.
- `app/rag/metrics.py`: edit. Adds `ndcg_at_k`, deduplication by section for ranking metrics, and the optional `set` field with the new completeness rules.
- `scripts/fetch_models.py`: edit. Fetches the embedding model and the reranker, each with its own file patterns.
- `scripts/search.py`: edit. Adds a `--rerank` flag.
- `scripts/ingest.py`: edit. A new `make_reranker(args)` next to `backends()`, so the cross-encoder (about 30 s to load) is built only when `--rerank` or the eval asks for it, not inside `backends()`. It returns the fake reranker in test mode and is shared by `search.py` and `eval_retrieval.py`. *Changed during the build, step 5.*
- `scripts/eval_retrieval.py`: edit. Adds the before/after table, the subset split, latency and the cut-off count.
- `evals/datasets/retrieval_golden.yaml`: edit. Version 0.2.0, with the reviewed hard entries appended. The original 30 are unchanged.
- `tests/unit/test_rag_rerank.py`: new.
- `tests/unit/test_rag_metrics.py`, `tests/unit/test_retrieval_golden.py`: edit. New cases. The completeness test changes on purpose (spec AC #5).
- `tests/fixtures/rag/golden_sample.yaml`: edit. One `set: hard` entry is added, so the integration test covers the subset split.
- `tests/integration/test_rag_cli.py`: edit. The fetch dry-run covers both models; adds `search --rerank` and the eval before/after.
- `docs/threat-model.md`: edit. T19 also covers the reranker.
- `docs/tech.md`, `docs/structure.md`, `README.md`: edit. The reranker, its commands and `rerank.py`.
- `docs/p3-reranking-eval/rerank-report.md`: new. The measured results.
- `docs/p3-reranking-eval/tasks.md`: new.
- `docs/index.md`: edit.
- `CLAUDE.md`: merge (step 10).

## Work sequence

1. **Draft the hard queries, and start the reranker download.**
   - I write `evals/datasets/retrieval_golden.hard-draft.yaml` from the P2 section list. It has at least 20 queries: about 7 for the security standard, 7 for the data spec, 3 for the synthetic spec and 3 for the SOP, with at least 8 per language.
   - Hardness rules: each query is a symptom or goal; it has no section numbers; it uses at most one content word from the gold section's title.
   - I check the draft's gold sections against the corpus and send it to you for review. **Your review runs in parallel with steps 2–6.**
   - The reranker download starts after you approve it.

   Spec AC #5 (draft part).
2. **Reranker module and config.** `configs/retrieval.yaml`, `app/rag/rerank.py`, `app/rag/search.py`, `tests/unit/test_rag_rerank.py`. Spec AC #1, #2, #3.
3. **Ranking metrics and golden-set schema.** `app/rag/metrics.py`, `tests/unit/test_rag_metrics.py`, `tests/unit/test_retrieval_golden.py`.
   - This step adds `set`, nDCG and deduplication, and pins the original 30 by content hash.
   - The "at least 20 hard queries" assertion is written here but stays **skipped** until step 7. It skips only while the golden set has no `set: hard` entry, so CI stays green in between.

   Spec AC #4, #5 (schema part).
4. **Model fetch for both models.** `scripts/fetch_models.py`, `tests/integration/test_rag_cli.py`. Spec AC #8.
5. **`search.py --rerank`.** `scripts/ingest.py` (`backends()` returns a reranker), `scripts/search.py`, `tests/integration/test_rag_cli.py`. Spec AC #7.
6. **Before/after eval.** `scripts/eval_retrieval.py`, `tests/fixtures/rag/golden_sample.yaml`, `tests/integration/test_rag_cli.py`. Spec AC #6.
7. **Merge the reviewed hard queries.** The entries you approved are appended to `evals/datasets/retrieval_golden.yaml` (version 0.2.0) with `reviewed_by` and `reviewed_at`. The draft file is deleted, and the hard-set assertion stops skipping. Spec AC #5.
8. **Threat model and steering docs.** `docs/threat-model.md`, `docs/tech.md`, `docs/structure.md`, `README.md`. Spec flagged concern: supply chain.
9. **Real measurements.**
   - Confirm the reranker is cached (`fetch_models.py`).
   - Run `scripts/eval_retrieval.py` on the real corpus and the full reviewed set, and paste the output.
   - Run `tests/unit/test_licence_hygiene.py` locally and paste the output.
   - Write `docs/p3-reranking-eval/rerank-report.md`.

   Spec AC #9, #10.
10. **`CLAUDE.md` merge.** Agent configuration, below.

**Parallel work:** step 1's review is your work and overlaps steps 2–6. Steps 2 and 3 share no files and have no dependency on each other, but they're small, so I'll do them in sequence in this session without worktrees.

## Test coverage

These rows from `test-scope-by-stage.md` matched:
- **Backend service / script only.** Each changed CLI gets a subprocess integration test. Applies to steps 4 (`fetch_models.py`), 5 (`search.py`) and 6 (`eval_retrieval.py`).
- **Phase 0 detected an existing project.** Regression for existing behaviour that a step changes. Applies to:
  - step 2: `Citation` gains a field, so every P2 search and citation test must pass unchanged;
  - step 3: the P2 metric tests stay unchanged; the golden completeness test changes on purpose, with a content-hash regression test for the original 30;
  - steps 4–6: the P2 CLI tests stay unchanged;
  - step 8: `test_repo_hygiene.py` stays unchanged.

Not matched:
- **Any database:** P3 changes no Qdrant collection config or payload, and no PostgreSQL schema. Reranking is computed at query time.
- **UI, accounts and roles:** none.

- **Step 1:** the draft is checked by the existing schema code (approved IDs, languages) and by the eval's gold-section existence check against the corpus. The output is pasted with the draft.
- **Step 2:** unit tests with the fake reranker and a crafted pool:
  - the output is a permutation of the input, with the same `citation_id`s;
  - scores are non-increasing and ranks are renumbered;
  - every §12.4 field is preserved;
  - the fake reorders the fused order, and is deterministic;
  - `select` returns `min(top_n, n)`;
  - the cross-encoder raises one-line errors when the extra is missing (patched import) and when the model isn't cached (patched loader).

  Regression: the P2 search and citation tests pass unchanged.
- **Step 3:**
  - Unit: MRR@5 and nDCG@5 against hand-computed cases (rank 1, rank 3, no hit, two gold sections, a repeated section counted once); `set` defaults to `original`; completeness errors when there are fewer than 20 hard queries, when a language has fewer than 8, or when a document isn't covered.
  - Regression: the content hash of the original 30 equals the P2 value, and the P2 metric tests pass unchanged.
- **Step 4:** integration: `--dry-run` prints both model IDs and both full revisions and downloads nothing. The P2 test that shadows the extra still passes.
- **Step 5:** integration: `search.py --rerank --test-backends` prints both rank columns, and the rerank flag without an ingested collection gives a one-line error.
- **Step 6:** integration: `eval_retrieval.py --test-backends` on the sample set prints the P2 table, the before/after table with original and hard rows, and candidate Recall@20. The JSON report shows that each reranked list is a permutation of the fused top 20.
- **Step 7:** the golden-set unit tests run against the real file, with the hard-set assertion now active.
- **Step 8:** documentation only. Checked at Phase 4 by quoting the T19 row.
- **Step 9:** pasted output: the eval run, latency, cut-off count, and the licence-hygiene test. The report's numbers must match the pasted output.
- **Step 10:** covered by the existing `agent-config-eval` workflow.

## Agent configuration changes

- **`CLAUDE.md` (step 10):**
  - Append to `## Commands`: `uv run python scripts/search.py "<query>" --rerank`, and a note on the existing `scripts/eval_retrieval.py` line that it now includes the before/after reranking table.
  - `## Test scope` and `## Common mistakes`: nothing new unless something comes up during the build; any addition is appended only.
  - You'll see the diff and approve it with `approve write`.
- **Hooks:** none added. Verified in Phase 0: `eval.sh` 6/6 plus the control-band self-check. The reranker download is a one-time setup step, like the BGE-M3 one.
- **`.claude/settings.json`:** unchanged.
- **CI:** no new job. `ci.yml` runs the new tests with default dependencies. `security-scan` already audits the `embed` extra (`--all-extras`, P2). `agent-config-eval` is present exactly once.
- **Subagents:** none.

## Rollback

- **Code:** revert the branch commits, or drop `feature/p3-reranking-eval`. P2's search is unchanged without `--rerank`.
- **Data:** nothing is stored, so there is nothing to delete in Qdrant. The golden set reverts with its commit; the original 30 are untouched either way.
- **Model:** delete `~/.cache/huggingface/hub/models--BAAI--bge-reranker-v2-m3`.
