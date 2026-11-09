# Intent: A cross-encoder reranker reorders the hybrid top-20, and its before/after effect is measured on a harder, reviewed query set
*Author: Kharisma Wardhana. Stage: Plan. Written: 2026-11-09. Via /aspec:make-it-simple-v2.*

## Problem statement

P2 left a working hybrid search and a reviewed 30-query set. But the PRD's P3 exit criterion, *recall/ranking metrics recorded*, isn't met:

- **No reranker exists.** TRD §12.3 puts a multilingual cross-encoder between hybrid retrieval (top 20–30) and the 5–8 passages the agent will read. Today the agent would get whatever RRF happened to put first. RRF's MRR@10 (0.753) is already below dense alone (0.759), so the ordering inside the top results is the weak spot.
- **The release gate can't be measured.** `reranked_recall_at_5 ≥ 0.85` needs a reranked result, and TRD §22.2 needs reranked MRR@5 and nDCG@5. It also needs a before/after comparison of the fused top 5 and the reranked top 5, taken from the same top-20 pool.
- **The current set can't show a difference.** At section level, hybrid already finds the right section in the top 5 for 29 of 30 queries. The 30 queries were written knowing the section titles, so they are easier than what real developers will ask. Any reranker would look "neutral", and that result would say nothing.

## Proposed outcome

- **A reranker on the local CPU.** A BGE reranker v2 M3-class cross-encoder, pinned by revision and loaded offline like the embedding model, scores the hybrid top-20 candidates for a query and returns them reordered with scores.
  - It is one function that later phases (the P4 `search_qris_spec` tool, the P5 `rerank_evidence` node) call.
  - It never changes which chunks are candidates. It only reorders them.
- **A harder query set.** About 20 symptom-style queries, phrased the way a developer describes a failing integration and not by section title, in Indonesian and English. Claude drafts them, the user reviews the gold sections, and they are added to the existing 30 to make one reviewed set of about 50.
- **A measured before/after report** on that set:
  - candidate Recall@20;
  - fused versus reranked Recall@5, MRR@5 and nDCG@5 from the same top-20 pool;
  - the same split for the original 30 and the harder queries;
  - reranker latency per query on CPU.

  The numbers are measured, not targets. The report states the result against the 0.85 gate, whatever it is.

## Affected users and systems

- Kharisma Wardhana: reviews the gold labels of the harder queries and runs the local eval.
- The local CPU: runs the reranker. The GPU pod is not used in P3.
- `app/rag/`: gains a reranking step and ranking metrics (nDCG). `search` itself is unchanged.
- `configs/retrieval.yaml`: reranker model, revision, candidate count, final count and limits.
- `evals/datasets/retrieval_golden.yaml`: grows from 30 to about 50 queries. Existing entries are unchanged.
- `scripts/eval_retrieval.py` and `scripts/fetch_models.py`: report reranked metrics, and fetch the reranker.
- CI: must keep passing with no reranker model, no PDFs and no services.
- Downstream: P4 (the `search_qris_spec` tool returns reranked results) and P5 (the `rerank_evidence` node).

## Constraints

- **Deadline:** P3 is done by 2026-11-10. All of P1–P9 must be done by 2026-11-20.
- **No GPU spend.** The reranker runs on local CPU. GPU budget is $19.57 left for P4–P9.
- **Data:** the reranker reads only chunk text already stored locally. ASPI text never leaves the machine, and excerpts in results stay capped at 300 characters.
- **Labels:** new queries are team-written paraphrases. No ASPI text is copied into the set. Only entries the user approved are marked reviewed.
- **Comparability:** the earlier 30 queries and their labels stay byte-identical, so P2's numbers stay comparable.
- **CI:** tests use a deterministic fake reranker. No model download in CI.
- **Pinning:** model revision and versions are pinned; nothing is guessed.
- **Untrusted text:** the reranker scores text; it never acts on it.

## Open questions

- Which reranker exactly (`BAAI/bge-reranker-v2-m3` or another in its class), at which revision and licence? Does it run through the already-installed sentence-transformers stack, so nothing new is needed?
- How slow is it on this CPU for 20 candidates of up to about 1,275 tokens? What `max_length` keeps it usable without cutting off the answer-bearing part of long chunks?
- How many candidates go into the reranker (TRD: 20–30), and how many come out for the agent (TRD: 5–8)? Should the selection step also enforce source-version consistency (§12.3 step 6) in P3, or leave that to P5?
- How should nDCG@5 be defined with section-level, binary labels (any chunk from a gold section is relevant; does a second chunk from the same section count again)?
- What makes a query "harder" in a way that is fair? How many in each language, and how are they spread across the four documents, so the set isn't tilted toward one?
- Should the reranker's latency be a measured value only, or should a limit be recorded now for P5's per-request budget?
