# Rerank report: p3-reranking-eval
*Author: Kharisma Wardhana. Stage: Test. Written: 2026-11-09. Via /aspec:make-it-simple-v2.*

**These are measured results** from one run on the developer machine, not targets (CLAUDE.md). Raw output: `evals/reports/retrieval-2026-11-09T073307.json` (git-ignored; it holds IDs, ranks, scores and timings only).

## Results

Measured on 2026-11-09 with `uv run python scripts/eval_retrieval.py`:
- 50 queries: 24 Indonesian and 26 English. Of these, 30 are the original P2 queries and 20 are the new hard ones.
- Dataset `retrieval_golden.yaml` 0.2.0.
- Fused and reranked results come from the **same** hybrid top-20 pool for each query.

| Subset | n | Candidate R@20 | Fused R@5 | Fused MRR@5 | Fused nDCG@5 | Reranked R@5 | Reranked MRR@5 | Reranked nDCG@5 |
|---|---|---|---|---|---|---|---|---|
| Original | 30 | 1.000 | **0.967** | 0.765 | 0.765 | 0.900 | 0.713 | 0.715 |
| Hard | 20 | 0.800 | 0.550 | 0.393 | 0.408 | **0.650** | **0.435** | **0.462** |
| All | 50 | 0.920 | 0.800 | 0.616 | 0.622 | 0.800 | 0.602 | 0.614 |

The P2 table on all 50 queries reads: hybrid R@5 0.800, R@10 0.860, R@20 0.920, MRR@10 0.625. Dense reads 0.780, 0.840, 0.900, 0.606, and sparse reads 0.540, 0.600, 0.660, 0.386.

On the original 30, fused R@5 (0.967) and candidate R@20 (1.000) match the P2 report exactly. The corpus and hybrid search are therefore unchanged since P2.

**Every reranked list was a permutation of its fused pool** (50 of 50, `is_permutation` in the JSON). No candidate was added or dropped (AC #1).

## Against the release gates (`configs/eval_thresholds.yaml`)

- **`reranked_recall_at_5` (≥ 0.85): measured 0.800 on all 50 queries, which is below the gate.**
  - On the original 30 it is 0.900, above the gate. On the hard 20 it is 0.650.
  - Without the reranker, all 50 also score 0.800, so the reranker neither closes nor widens the gap.
- **`rag_candidate_recall_at_20` (≥ 0.90): measured 0.920 on all 50, which meets the gate.** On the hard 20 it is 0.800.
  - Four hard queries (h12, h17, h19, h20) have no gold section anywhere in the top 20.
  - No reranker can fix those four. That ceiling belongs to retrieval, not reranking.
- These are release gates for P9. **No pass/fail is applied in P3.** The spec asks for the numbers to be stated against the gates, and they are stated here.

## Latency (CPU)

| Per query, 20 pairs | Measured | Planning budget |
|---|---|---|
| Median | **161.65 s** | — |
| p95 | **233.31 s** | 10 s |
| Max | **3,222.61 s** | — |

- **Pairs cut off by `max_length` (1536): 0.**
- **The whole run took 224 min of wall time.** Nearly all of it was reranking.
- **p95 is about 23 times the 10 s planning budget.** Even the fastest query took 71.5 s (q07).
- **Two outliers: q17 at 3,223 s and h07 at 2,563 s.**
  - All other queries took between 71 and 233 s.
  - The cause was not investigated. It is consistent with the machine sleeping or being busy with other work during those queries, but that is not verified.
  - The median and p95 are barely affected by the two outliers. The maximum is entirely their result.
- The machine has 18 logical CPUs. Ingestion was not running in parallel. Torch thread settings were left at their defaults.

## Where reranking moved the first relevant result

The rank of the first chunk from a gold section, fused → reranked (lower is better). 21 of 50 queries changed; 29 did not.

| Better | Worse |
|---|---|
| h03 15 → 3, h16 15 → 8, h01 5 → 1, q04 5 → 1, h13 7 → 5, h15 6 → 4, h06 14 → 11, h18 3 → 1, q27 2 → 1, q28 2 → 1, q12 3 → 2 | h04 1 → 18, q24 7 → 16, q09 1 → 8, q22 4 → 9, q17 1 → 5, q30 1 → 5, q23 1 → 3, h02 2 → 3, h05 2 → 3, h09 3 → 4 |

- **Hard queries mostly gain (7 better, 4 worse).** These are symptom-style questions, and the cross-encoder reads them better than RRF does.
- **Original queries mostly lose (4 better, 6 worse).** These were written close to section wording, so the fused order already put the right section first. The reranker sometimes moves a neighbouring section above it.

## Read these numbers with their limits

- **Small sets.** On the hard 20, one query changes R@5 by 0.05. On the original 30, one query changes it by 0.033. The differences above are a handful of queries each way, not a stable effect size.
- **Section-level labels.** A result that comes from the gold section counts as a hit, even if the chunk itself doesn't answer the question. Some reranker "losses" may rank an answering passage from a sibling section above a gold one. Passage-level labels are out of scope (spec).
- **One author.** Claude wrote both query sets and the system; you reviewed every label. The hard set follows written hardness rules, but real developer questions may still be harder.
- **CPU only, defaults only.** No tuning was tried: candidate count, `max_length`, batch size, threads, quantisation or GPU. The latency here is the latency of this configuration on this machine.

## What this leaves for P5 (not decided here)

As the spec's flagged concern says, the latency fix is **Kharisma Wardhana's decision, in P5, with these numbers**. The measured options are:

- use fewer candidates;
- use a shorter `max_length`;
- move reranking to the GPU pod;
- skip reranking for queries where the fused order is already confident.

**A second decision is now on the table:** whether to rerank at all. The measurements say:

- on all 50 queries, reranking gives **no net gain in R@5** and a small loss in MRR@5 and nDCG@5;
- it **helps the hard (symptom-style) queries**;
- it **hurts the original (title-style) ones**.

## What was measured on

| Item | Value |
|---|---|
| Corpus | P2 corpus unchanged: 4 approved sources, 278 chunks in `qris_kb_v1` (see the P2 retrieval report) |
| Embedding and search | as P2: `BAAI/bge-m3` @ `5617a9f`, dense and sparse prefetch 30 each, RRF, top 20 |
| Reranker | `BAAI/bge-reranker-v2-m3` @ `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`, safetensors, CPU, offline |
| Rerank config | `max_length` 1536, candidates 20, `top_n` 6, `batch_size` 4, `latency_budget_p95_s` 10 (`configs/retrieval.yaml`) |
| Retrieval set | `evals/datasets/retrieval_golden.yaml` 0.2.0: 30 original (P2) plus 20 hard, all reviewed 2026-11-09 |
| Metrics | Recall@5, MRR@5, and nDCG@5 with binary section-level gains, where each gold section counts once (spec) |
| Licence hygiene | `tests/unit/test_licence_hygiene.py` 2 passed locally with the PDFs present, 2026-11-09 |
