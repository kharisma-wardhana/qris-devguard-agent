# Spec: A cross-encoder reranker reorders the hybrid top-20, and its before/after effect is measured on a harder, reviewed query set
*Author: Kharisma Wardhana. Stage: Design. Written: 2026-11-09. Via /aspec:make-it-simple-v2.*
*Implements: docs/p3-reranking-eval/intent.md*

## Behaviour

1. **One-time setup.** `uv run python scripts/fetch_models.py` now downloads the reranker as well as BGE-M3. Each model is pinned to its own revision. `--dry-run` prints both models and does nothing else.
2. **Rerank.** `rerank(run, reranker)` takes one hybrid search run (top 20 candidates by default) and returns the same candidates reordered by cross-encoder score, highest first.
   - Each result keeps its `citation_id`, `chunk_id` and every citation field. It gains a `rerank_score` and a new `rank`.
   - No candidate is added or dropped.
   - `select(reranked, top_n)` then takes the first `top_n` (default 6, from config) for the agent's context.
3. **Search with reranking.** `scripts/search.py "<query>" --rerank` prints the reranked list with both the fused rank and the reranked rank, so you can see what moved.
4. **Measure.** `scripts/eval_retrieval.py` keeps printing P2's dense/sparse/hybrid table, unchanged, and adds a before/after table. For every query it takes the **same** hybrid top-20 pool and computes:
   - candidate Recall@20, which is the same before and after, because reranking can't add candidates;
   - fused versus reranked Recall@5, MRR@5 and nDCG@5;
   - each of these for the original set, the hard set, and both together.

   It also prints reranker latency per query (median, p95, max) and the number of query–chunk pairs cut off by `max_length`. The full per-query results go to the git-ignored `evals/reports/`.
5. **Harder queries.** The labelled set gains a `hard` subset of at least 20 queries.
   - They are written as a developer describes a failing integration or a goal, not by section title.
   - They are tagged `set: hard`. The original 30 entries stay byte-identical, and an entry without `set` is in the original set.

## Architecture and the reason for it

Follows `docs/tech.md` and `docs/structure.md`, and P2's settled decisions: logic in `app/rag/` with thin CLIs, parameters in `configs/retrieval.yaml`, models pinned, loaded offline, CPU only, and fakes in CI. It doesn't depart from them.

| Decision | Reason |
|---|---|
| **Model: `BAAI/bge-reranker-v2-m3` at revision `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`** (Apache-2.0, an XLM-RoBERTa cross-encoder, 2.27 GB safetensors, 8,194 positions) | The TRD's named class. It is multilingual (Indonesian + English) and its licence allows use. Safetensors weights can't run code at load time |
| **Run through `sentence_transformers.CrossEncoder`** (6.2.0, already in the `embed` extra) on CPU. It loads offline (`HF_HUB_OFFLINE=1`) | No new dependency. Same loading and pinning pattern as BGE-M3 (P2) |
| **`max_length` 1536 tokens** for the query–chunk pair | P2 measured chunks up to 1,275 tokens. 1536 fits the longest chunk plus a query, so the answer-bearing tail isn't cut off. Cut-off pairs are still counted and reported |
| **Candidates in: 20 (the hybrid `limit`). Passages out: `top_n` 6** | TRD §12.3: 20–30 candidates, then 5–8 passages. 20 keeps CPU time down, and §22.2 defines the comparison on a top-20 pool |
| **Reranking only reorders.** The output is the same set of `Citation`s with the same `citation_id`s | The citation validator (P2) checks "retrieved in this run" by ID, so reranked results stay valid citations. It also makes AC #1 testable |
| **Version consistency (§12.3 step 6) is not applied in P3** | It depends on which version the user asked about, and that context only exists in the agent (P5). §12.5 forbids dropping one version silently, so P3 keeps both |
| **`FakeReranker`** (deterministic: the share of query terms found in the chunk) behind the same interface, used by tests and `--test-backends` | CI has no model (P2 rule). Its scoring differs from the fused order, so tests can tell that reranking happened |
| **Metrics with binary section-level labels:** a result is relevant if its `(document_id, section_id)` is gold and it is the **first** result from that section in the list. Later chunks of the same section count 0. Ideal DCG = min(number of gold sections, 5) relevant results at the top. MRR@5 = 1/rank of the first relevant result | Long sections (for example §4.3.5, 43 pages) would otherwise fill the top 5 and inflate nDCG. Counting each gold section once rewards finding all the gold sections, not repeats |
| **Golden-set schema:** an optional `set` field (`original` by default, or `hard`). The completeness check becomes "exactly 30 original queries, at least 20 hard queries, at least 8 per language in the hard set, every document covered by the hard set, every entry reviewed" | Keeps the P2 entries byte-identical while adding the harder subset. **This changes P2's "exactly 30" check;** a regression test pins the original 30 by content hash |
| **`fetch_models.py` fetches a list of models from config** (embedding and reranker), each with its own file patterns | One setup command. The reranker needs only `*.json`, `*.model` and `model.safetensors` |
| **Latency planning budget: reranking 20 candidates ≤ 10 s at p95 on this CPU.** Recorded, not a P3 pass/fail | One third of the PRD's 30 s median target for a cited diagnosis (§6). If the measurement is over budget, P5's options are fewer candidates, a shorter `max_length`, or the GPU, decided there with the numbers |

## Data

- **`configs/retrieval.yaml`** gains a `rerank` block: `model_id`, `revision`, `max_length`, `candidates`, `top_n`, `batch_size`, `latency_budget_p95_s`.
- **`evals/datasets/retrieval_golden.yaml`:**
  - dataset version 0.2.0;
  - the 30 original entries are unchanged;
  - at least 20 new entries with `set: hard`, written by Claude and reviewed by the user, with `reviewed_by` and `reviewed_at` only on the entries the user approved;
  - no ASPI text, only team-written queries plus IDs.
- **Reports** (git-ignored `evals/reports/`): per-query fused and reranked ranks, scores and latencies. They hold IDs and numbers, never chunk text. A committed `docs/p3-reranking-eval/rerank-report.md` holds the measured tables.
- **Model weights:** about 2.27 GB more in the Hugging Face cache, outside the repo.
- **Nothing new is stored** in Qdrant or PostgreSQL. Reranking is computed at query time.
- **Secrets:** none.

## Acceptance criteria

1. **Reranking only reorders.** For any search run, `rerank` returns:
   - exactly the input candidates (same `chunk_id`s and `citation_id`s, same count);
   - in non-increasing `rerank_score` order;
   - with ranks renumbered from 1.

   Every §12.4 field is unchanged. Unit test with the fake reranker.
2. **`select` respects `top_n`.** It returns the first `min(top_n, n)` reranked results, and `top_n` comes from config (6). Unit test.
3. **The reranker backends behave.**
   - The fake is deterministic, and on a crafted pool it reorders the fused order.
   - The cross-encoder raises a one-line `uv sync --extra embed` error when the extra is missing, and a one-line `fetch_models.py` error when the model isn't cached.

   Unit tests.
4. **Ranking metrics are correct.** MRR@5 and nDCG@5 match hand-computed values for these cases:
   - one gold section hit at rank 1, 3 and none;
   - two gold sections;
   - a repeated chunk from one gold section, counted once.

   Unit tests.
5. **The labelled set is extended without changing the old entries.**
   - The original 30 entries' content hash equals the P2 value.
   - The hard subset has at least 20 entries, at least 8 per language, and covers all 4 documents.
   - Every entry is reviewed, and every gold pair names an approved document.

   Unit test. The check that each gold section exists in the corpus runs during the eval.
6. **The before/after comparison uses the same pool.** `scripts/eval_retrieval.py --test-backends` on a sample set prints:
   - the P2 three-mode table;
   - the before/after table with rows for fused and reranked;
   - candidate Recall@20.

   The JSON report shows, for every query, that the reranked list is a permutation of the fused top-20. Integration test (subprocess).
7. **`search.py --rerank` works.** With test backends, it prints the reranked results with both fused and reranked ranks. Integration test.
8. **Model fetch covers both models.** `fetch_models.py --dry-run` prints both model IDs and both full revisions, and downloads nothing. Integration test. Without the extra, the existing one-line error test still passes.
9. **Real measurements locally.**
   - With both models cached and the P2 corpus ingested, the eval on the full reviewed set runs. Its pasted output gives every number in Behaviour 4: per subset, latency median/p95/max, and the cut-off pair count.
   - The numbers go into `docs/p3-reranking-eval/rerank-report.md`, labelled as measured, with date, dataset version and config.
   - The report states reranked Recall@5 against the 0.85 gate and p95 latency against the 10 s planning budget, **whatever the results are**.
10. **Hygiene and CI.**
    - `tests/unit/test_licence_hygiene.py` passes locally with the PDFs present (output pasted).
    - In a fresh clone with no extras, PDFs or services, `uv run pytest` passes, and the only skips are the licence tests with their stated reason.
    - Every earlier test passes unchanged, except the golden-set completeness check, which changes on purpose (AC #5) and has a regression test.

## Flagged concerns

- **CPU latency could be high.** Twenty pairs of up to 1,536 tokens through a 568M-parameter model on 8 cores may take several seconds or more per query. P3 measures it against the 10 s planning budget. If it's over, the fix is chosen in P5 (fewer candidates, shorter `max_length`, or GPU). **Decides:** Kharisma Wardhana, with the numbers, in P5.
- **Who wrote the hard queries.** Claude writes the hard queries and also built the system, so they may still be easier than real ones. Mitigations:
  - rules for hardness are written into the plan: no section numbers, and at most one content word from the gold section's title;
  - you review every label;
  - the report presents the original and hard sets separately.

  **Decides:** Kharisma Wardhana, at label review.
- **Supply chain: a second 2.27 GB model.** It is pinned by full SHA, uses safetensors (no pickle), loads offline, and its download is the same one-time setup exception as BGE-M3. The `docs/threat-model.md` T19 row is extended to the reranker. **Decides:** Kharisma Wardhana.
- **A P2 test's behaviour changes on purpose.** "Exactly 30 queries" becomes "exactly 30 original plus at least 20 hard". The original 30 are pinned by content hash, so P2's numbers stay comparable. **Decides:** Kharisma Wardhana (accepted by choosing the harder-queries option).
- **Deadline.** 2026-11-10 is tomorrow. The critical path is the model download (about 2.3 GB) and your review of the hard labels. **Decides:** Kharisma Wardhana.
- **Licence.** ASPI chunk text goes through the reranker only on the local machine. Reports hold IDs and scores. New queries are paraphrases, and the licence-hygiene test runs locally (AC #10). No new decision needed.
- **Privacy, accessibility and UX:** none found. Checks covered: there's no personal data in queries or corpus, and the only interface is the CLI.

## Out of scope

- Enforcing source-version consistency when selecting passages (P5, where the user's version context exists).
- Passage-level (chunk) gold labels. Section-level labels plus the hard subset were chosen instead.
- Tuning chunk size, BM25, RRF or `top_n` against the set. P3 measures the first reranker configuration. Tuning would need a held-out split that the set is too small for.
- Running the reranker on the GPU pod, or through vLLM.
- The P4 `search_qris_spec` tool and the P5 `rerank_evidence` node, which will call `rerank` and `select`.
- Langfuse tracing of reranking (P8).

## Resolved questions

- **Which reranker, revision and licence; does it fit the installed stack?** `BAAI/bge-reranker-v2-m3` at `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`, Apache-2.0, safetensors. It runs through `sentence_transformers.CrossEncoder` 6.2.0, which is already in the `embed` extra, so no new package is needed.
- **How slow on this CPU, and what `max_length`?** `max_length` 1536, covering P2's longest chunk (1,275 tokens) plus the query. Speed is measured in AC #9, not assumed, against a 10 s p95 planning budget.
- **How many in and out; version consistency now?** 20 candidates in and 6 out (TRD 20–30 and 5–8). Version consistency is deferred to P5; P3 never drops a version.
- **nDCG@5 with section-level binary labels?** A result counts if it is the first from a gold section; repeats count 0. Ideal DCG uses min(number of gold sections, 5) hits.
- **What makes a query fairly "harder", and how many?** At least 20: at least 8 Indonesian and at least 8 English, covering all 4 documents (target: 7 security standard, 7 data spec, 3 synthetic spec, 3 SOP). Each is written as a symptom or goal, with no section numbers and at most one content word from the gold section's title. Each is reviewed by you.
- **Measured only, or a latency limit for P5?** Both: measured in P3, plus a recorded planning budget of 10 s p95 for reranking 20 candidates, one third of the PRD's 30 s target. It isn't a P3 pass/fail; P5 decides with the numbers.
