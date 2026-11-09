"""Search the knowledge base (p2 spec Behaviour 3).

Usage:
  uv run python scripts/search.py "X-SIGNATURE tidak valid"
  uv run python scripts/search.py "timestamp format" --mode sparse --limit 5 --json

Result text is untrusted data. Excerpts are capped by licence (300 chars for reference-only).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from ingest import add_backend_args, backends  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hybrid search over the knowledge base.")
    parser.add_argument("query")
    parser.add_argument("--mode", choices=("hybrid", "dense", "sparse"), default="hybrid")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--json", action="store_true", help="print one JSON object per result")
    add_backend_args(parser)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")       # Windows consoles default to cp1252

    from app.rag.embedding import EmbedStackMissing
    from app.rag.search import search

    try:
        store, embedder = backends(args)
        if not store.client.collection_exists(store.collection):
            print(f"error: collection {store.collection} not found: run scripts/ingest.py first", file=sys.stderr)
            return 1
        run = search(store, embedder, args.query, mode=args.mode, limit=args.limit)
    except EmbedStackMissing as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SystemExit:
        raise
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {str(exc).splitlines()[0][:200] if str(exc) else ''}", file=sys.stderr)
        return 1

    for r in run.results:
        if args.json:
            print(json.dumps(r.as_dict(), ensure_ascii=False))
        else:
            page = f"p.{r.page}" if r.page is not None else "-"
            snippet = r.quote_or_excerpt.replace("\n", " ")[:110]
            print(f"{r.rank:>2} {r.citation_id} {r.score:.4f} {r.document_id} v{r.document_version} "
                  f"§{r.section_id} {page} | {snippet}")
    if not run.results:
        print("no results")
    return 0


if __name__ == "__main__":
    sys.exit(main())
