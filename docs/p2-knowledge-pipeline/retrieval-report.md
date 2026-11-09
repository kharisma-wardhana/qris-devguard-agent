# Retrieval report: p2-knowledge-pipeline
*Author: Kharisma Wardhana. Stage: Test. Written: 2026-11-09. Via /aspec:make-it-simple-v2.*

**These are measured results** from one run on the developer machine, not targets (CLAUDE.md). Raw output: `evals/reports/retrieval-2026-11-09T023001.json` (git-ignored; IDs, ranks and scores only).

## Results

Run on 2026-11-09 with `uv run python scripts/eval_retrieval.py`. 30 queries (14 Indonesian, 16 English), top 20 per query.

| Mode | Recall@5 | Recall@10 | Recall@20 | MRR@10 |
|---|---|---|---|---|
| **Hybrid (RRF)** | **0.967** | **1.000** | **1.000** | 0.753 |
| Dense (BGE-M3) | 0.933 | 0.933 | 0.967 | 0.759 |
| Sparse (BM25) | 0.667 | 0.700 | 0.767 | 0.495 |

A query is a hit at k if any of its top-k chunks belongs to one of its gold sections.

**Queries outside the top 5:**
- Hybrid: q24 (balance check, first hit at rank 7).
- Dense: q09 (data format and encoding, no hit in the top 20), q28 (synthetic Query Payment headers, rank 11).
- Sparse: q04, q05, q08, q10, q22, q24 and q29 (no hit in the top 20); q12 (rank 6), q15 (rank 11), q30 (rank 15).

## Against the release gates (`configs/eval_thresholds.yaml`)

- `rag_candidate_recall_at_20` (≥ 0.90): hybrid measured **1.000**. This is the candidate pool P3's reranker will work from.
- `reranked_recall_at_5` (≥ 0.85) is a P3 measurement. For comparison, the un-reranked hybrid Recall@5 is 0.967.
- These gates are release gates for P3 and P9. **No pass/fail applies in P2.**

## Read these numbers with their limits

- **Section-level labels on a coarse document.** The data spec splits into 58 sections over 585 pages. Some sections are long; §4.3.5 QR MPM, for example, covers 43 pages and many chunks. A hit on the right section is easier than a hit on the passage that actually answers the question. P3 can label passages if this proves too lenient.
- **Small set, one author.** There are 30 queries. The same author (Claude) wrote the queries and the system, and you reviewed the labels. Queries written with knowledge of section titles can be easier than real user questions, so these numbers are likely optimistic.
- **Fusion and ranking.** Hybrid has the best recall, but its MRR@10 (0.753) is slightly below dense (0.759): RRF sometimes pushes the first relevant chunk down a rank or two. Improving order within the top results is the reranker's job (P3).
- **Why sparse is weak:** many queries are paraphrases, and English queries are matched against Indonesian text. The encoder doesn't stem, so word-form variants don't match. Sparse still adds value inside hybrid, where it supplies exact identifiers.

## What was measured on

| Item | Value |
|---|---|
| Corpus | 4 approved sources, 278 chunks in `qris_kb_v1` |
| `snap-technical-security-standard` 1.0.2 | 33 pages, 31 sections, 37 chunks; sha256 `c60c3305…f902a` |
| `snap-data-technical-spec` 1.0.2 | 585 pages, 58 sections, 213 chunks; sha256 `5ba81809…eeda` |
| `synthetic-qris-mpm-service-spec` 0.1.0 | 18 sections, 18 chunks; sha256 `ffc038ee…0e3b` |
| `synthetic-qris-troubleshooting-sop` 0.1.0 | 10 sections, 10 chunks; sha256 `e6c5fdf9…940f` |
| Embedding | `BAAI/bge-m3` @ `5617a9f`, CPU, `max_seq_length` 1536, 0 truncated chunks |
| Chunking | target 600, max 800, overlap 80 estimated tokens (characters ÷ 4); PDF headings must be bold |
| Sparse | BM25 k1 1.2, b 0.75, `avg_doc_len` 300, Qdrant IDF |
| Search | dense and sparse prefetch 30 each, RRF, top 20 |
| Retrieval set | `evals/datasets/retrieval_golden.yaml` v0.1.0, reviewed 2026-11-09 |

## Ingestion measurements

- **Full ingestion** of all 4 sources took **2,530 s** of wall time (about 42 minutes) on the 8-core CPU. That's an upper bound: PDF extraction for the section listing ran in parallel on the same CPU. The data spec dominates the time (585 pages).
- **A re-run with nothing changed took 28 s,** mostly model load. All 4 documents reported `unchanged`.
- **Real chunk sizes** in BGE-M3 tokens: the data spec has a median of 3.25 characters per token (the estimate assumes 4), and its longest chunk is 1,275 tokens. That led to `max_seq_length` 1536 (decided by you, 2026-11-09).
