"""Measure retrieval on the labelled query set (p2 spec Behaviour 5, AC #13).

Usage:
  uv run python scripts/eval_retrieval.py                       # real corpus, 30-query set
  uv run python scripts/eval_retrieval.py --golden other.yaml

Runs every query in dense, sparse and hybrid mode (top 20), prints Recall@5/10/20 and
MRR@10 per mode, and writes per-query ranks (no document text) to evals/reports/.
Fails if a gold section doesn't exist in the ingested corpus. The numbers are measured
results; no pass/fail threshold applies in P2 (the release gates are P3/P9).
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from ingest import DEFAULT_MANIFEST, DEFAULT_REPORTS, add_backend_args, backends, make_reranker  # noqa: E402

DEFAULT_GOLDEN = ROOT / "evals" / "datasets" / "retrieval_golden.yaml"
KS = (5, 10, 20)
SUBSETS = ("original", "hard", "all")


def p95(values: list[float]) -> float:
    """Nearest-rank 95th percentile."""
    ordered = sorted(values)
    return ordered[max(math.ceil(0.95 * len(ordered)) - 1, 0)] if ordered else 0.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Retrieval eval: dense, sparse, hybrid.")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORTS)
    parser.add_argument("--no-rerank", action="store_true", help="skip the p3 before/after reranking table")
    add_backend_args(parser)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    from app.rag import config, manifest, metrics
    from app.rag.embedding import EmbedStackMissing
    from app.rag.search import MODES, search

    if not args.golden.is_file():
        print(f"error: golden set not found: {args.golden}", file=sys.stderr)
        return 1
    try:
        meta, queries = metrics.load_golden(args.golden)
    except Exception as exc:            # malformed YAML or entries: one line, no traceback (found in p3 step 1)
        print(f"error: golden set unreadable: {type(exc).__name__}: {str(exc).splitlines()[0][:160] if str(exc) else ''}",
              file=sys.stderr)
        return 1
    approved = {e["document_id"] for e in manifest.load_manifest(args.manifest) if e.get("approval_status") == "approved"}
    errors = metrics.schema_errors(queries, approved, complete=False)
    if errors:
        print("error: golden set invalid: " + "; ".join(errors[:5]), file=sys.stderr)
        return 1
    try:
        store, embedder = backends(args)
        corpus = {(p["document_id"], p["section_id"]) for p in store.payloads()}
        missing = sorted({f"{q.id}:{d}#{s}" for q in queries for d, s in q.gold if (d, s) not in corpus})
        if missing:
            print(f"error: gold sections not in the corpus: {', '.join(missing[:10])}", file=sys.stderr)
            return 1
        limit = max(KS)
        per_query, summary, pools = [], {}, {}
        for mode in MODES:
            runs = []
            for q in queries:
                run = search(store, embedder, q.query, mode=mode, limit=limit)
                if mode == "hybrid":
                    pools[q.id] = run                    # the same top-20 pool the reranker gets
                ranked = [(r.document_id, r.section_id) for r in run.results]
                runs.append((ranked, set(q.gold)))
                per_query.append({"id": q.id, "language": q.language, "mode": mode,
                                  "first_hit_rank": metrics.first_hit_rank(ranked, set(q.gold), limit),
                                  "top_sections": [f"{d}#{s}" for d, s in ranked[:5]]})
            summary[mode] = {f"recall@{k}": round(metrics.recall_at_k(runs, k), 4) for k in KS}
            summary[mode]["mrr@10"] = round(metrics.mrr_at_k(runs, 10), 4)
        before_after, rerank_rows, latency = {}, [], {}
        if not args.no_rerank:
            from app.rag.rerank import rerank

            reranker = make_reranker(args)
            pairs = {"fused": {s: [] for s in SUBSETS}, "reranked": {s: [] for s in SUBSETS}}
            seconds, truncated = [], 0
            for q in queries:
                pool = pools[q.id]
                reranked, stats = rerank(pool, reranker)
                seconds.append(stats.seconds)
                truncated += stats.truncated_pairs or 0
                gold = set(q.gold)
                fused_ranked = [(r.document_id, r.section_id) for r in pool.results]
                rr_ranked = [(r.document_id, r.section_id) for r in reranked.results]
                for subset in (q.subset, "all"):
                    pairs["fused"][subset].append((fused_ranked, gold))
                    pairs["reranked"][subset].append((rr_ranked, gold))
                rerank_rows.append({
                    "id": q.id, "subset": q.subset, "language": q.language, "seconds": round(stats.seconds, 3),
                    "is_permutation": sorted(r.chunk_id for r in reranked.results)
                    == sorted(r.chunk_id for r in pool.results),
                    "fused_first_hit": metrics.first_hit_rank(fused_ranked, gold, limit),
                    "reranked_first_hit": metrics.first_hit_rank(rr_ranked, gold, limit),
                    "reranked_top_sections": [f"{d}#{s}" for d, s in rr_ranked[:5]]})
            for subset in SUBSETS:
                f, r = pairs["fused"][subset], pairs["reranked"][subset]
                before_after[subset] = {
                    "n": len(f), "candidate_recall@20": round(metrics.recall_at_k(f, limit), 4),
                    **{f"{name}_{metric}@5": round(fn(rows, 5), 4)
                       for name, rows in (("fused", f), ("reranked", r))
                       for metric, fn in (("recall", metrics.recall_at_k), ("mrr", metrics.mrr_at_k),
                                          ("ndcg", metrics.ndcg_at_k))}}
            latency = {"median_s": round(statistics.median(seconds), 3) if seconds else 0.0,
                       "p95_s": round(p95(seconds), 3), "max_s": round(max(seconds, default=0.0), 3),
                       "budget_p95_s": config.retrieval()["rerank"]["latency_budget_p95_s"],
                       "truncated_pairs": None if args.test_backends else truncated}
    except EmbedStackMissing as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SystemExit:
        raise
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {str(exc).splitlines()[0][:200] if str(exc) else ''}", file=sys.stderr)
        return 1

    print(f"queries: {len(queries)}  (id: {sum(q.language == 'id' for q in queries)}, "
          f"en: {sum(q.language == 'en' for q in queries)})  dataset: {meta.get('dataset_version')}")
    print(f"{'mode':<8} {'R@5':>6} {'R@10':>6} {'R@20':>6} {'MRR@10':>7}")
    for mode in MODES:
        s = summary[mode]
        print(f"{mode:<8} {s['recall@5']:>6.3f} {s['recall@10']:>6.3f} {s['recall@20']:>6.3f} {s['mrr@10']:>7.3f}")
    if before_after:
        rcfg = config.retrieval()["rerank"]
        # no blank line before this header: p2's test reads the first word of every line
        print(f"before/after on the same hybrid top-{limit} pool  "
              f"(reranker {rcfg['model_id']}@{rcfg['revision'][:7]}{', FAKE' if args.test_backends else ''})")
        print(f"{'subset':<9}{'n':>3}  {'cand R@20':>9} | {'fused R@5':>9} {'MRR@5':>6} {'nDCG@5':>6} "
              f"| {'rerank R@5':>10} {'MRR@5':>6} {'nDCG@5':>6}")
        for subset in SUBSETS:
            b = before_after[subset]
            if not b["n"]:
                continue
            print(f"{subset:<9}{b['n']:>3}  {b['candidate_recall@20']:>9.3f} | {b['fused_recall@5']:>9.3f} "
                  f"{b['fused_mrr@5']:>6.3f} {b['fused_ndcg@5']:>6.3f} | {b['reranked_recall@5']:>10.3f} "
                  f"{b['reranked_mrr@5']:>6.3f} {b['reranked_ndcg@5']:>6.3f}")
        trunc = "n/a (fake)" if latency["truncated_pairs"] is None else latency["truncated_pairs"]
        print(f"rerank latency per query: median {latency['median_s']:.2f} s, p95 {latency['p95_s']:.2f} s, "
              f"max {latency['max_s']:.2f} s (planning budget p95 {latency['budget_p95_s']} s); "
              f"pairs over max_length: {trunc}")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    args.report_dir.mkdir(parents=True, exist_ok=True)
    out = args.report_dir / f"retrieval-{now.replace(':', '').replace('+', 'Z')[:17]}.json"
    out.write_text(json.dumps({"measured_at": now, "dataset_version": meta.get("dataset_version"),
                               "config": {"chunking": config.retrieval()["chunking"],
                                          "sparse": config.retrieval()["sparse"],
                                          "search": config.retrieval()["search"],
                                          "embedding": config.retrieval()["embedding"],
                                          "rerank": config.retrieval()["rerank"]},
                               "summary": summary, "per_query": per_query,
                               "before_after": before_after, "rerank_latency": latency,
                               "rerank_per_query": rerank_rows}, indent=2), encoding="utf-8")
    print(f"report: {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
