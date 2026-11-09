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
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from ingest import DEFAULT_MANIFEST, DEFAULT_REPORTS, add_backend_args, backends  # noqa: E402

DEFAULT_GOLDEN = ROOT / "evals" / "datasets" / "retrieval_golden.yaml"
KS = (5, 10, 20)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Retrieval eval: dense, sparse, hybrid.")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORTS)
    add_backend_args(parser)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    from app.rag import config, manifest, metrics
    from app.rag.embedding import EmbedStackMissing
    from app.rag.search import MODES, search

    if not args.golden.is_file():
        print(f"error: golden set not found: {args.golden}", file=sys.stderr)
        return 1
    meta, queries = metrics.load_golden(args.golden)
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
        per_query, summary = [], {}
        for mode in MODES:
            runs = []
            for q in queries:
                run = search(store, embedder, q.query, mode=mode, limit=limit)
                ranked = [(r.document_id, r.section_id) for r in run.results]
                runs.append((ranked, set(q.gold)))
                per_query.append({"id": q.id, "language": q.language, "mode": mode,
                                  "first_hit_rank": metrics.first_hit_rank(ranked, set(q.gold), limit),
                                  "top_sections": [f"{d}#{s}" for d, s in ranked[:5]]})
            summary[mode] = {f"recall@{k}": round(metrics.recall_at_k(runs, k), 4) for k in KS}
            summary[mode]["mrr@10"] = round(metrics.mrr_at_k(runs, 10), 4)
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
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    args.report_dir.mkdir(parents=True, exist_ok=True)
    out = args.report_dir / f"retrieval-{now.replace(':', '').replace('+', 'Z')[:17]}.json"
    out.write_text(json.dumps({"measured_at": now, "dataset_version": meta.get("dataset_version"),
                               "config": {"chunking": config.retrieval()["chunking"],
                                          "sparse": config.retrieval()["sparse"],
                                          "search": config.retrieval()["search"],
                                          "embedding": config.retrieval()["embedding"]},
                               "summary": summary, "per_query": per_query}, indent=2), encoding="utf-8")
    print(f"report: {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
