"""Ingest the approved sources in the manifest into Qdrant (p2 spec Behaviour 2).

Usage:
  uv run python scripts/ingest.py                       # real: BGE-M3 (embed extra) + Qdrant server
  uv run python scripts/ingest.py --manifest path.yaml --root path

Test mode (CI and integration tests only): `--test-backends --qdrant-path DIR` uses an
on-disk local Qdrant and the deterministic fake embedder. It is refused unless
DEVGUARD_TEST_BACKENDS=1 is set, so it can't be used by accident for real data.

Exit codes: 0 all good, 1 a document failed or an error occurred, 2 setup missing.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_MANIFEST = ROOT / "knowledge_base" / "manifests" / "sources.yaml"
DEFAULT_REPORTS = ROOT / "evals" / "reports"


def backends(args):
    """(store, embedder) for real or test mode. Shared by scripts/search.py and eval_retrieval.py."""
    from app.rag.embedding import BgeM3Embedder, FakeEmbedder
    from app.rag.store import Store, make_client

    if args.test_backends:
        if os.environ.get("DEVGUARD_TEST_BACKENDS") != "1":
            raise SystemExit("error: --test-backends is for tests only (set DEVGUARD_TEST_BACKENDS=1)")
        if not args.qdrant_path:
            raise SystemExit("error: --test-backends needs --qdrant-path")
        return Store(make_client(Path(args.qdrant_path))), FakeEmbedder()
    return Store(make_client()), BgeM3Embedder()


def add_backend_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--test-backends", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--qdrant-path", help=argparse.SUPPRESS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest approved sources into Qdrant.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--root", type=Path, default=ROOT, help="repo root that local_path is relative to")
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORTS)
    add_backend_args(parser)
    args = parser.parse_args(argv)

    from app.rag.embedding import EmbedStackMissing
    from app.rag.ingest import ingest, write_report

    if not args.manifest.is_file():
        print(f"error: manifest not found: {args.manifest}", file=sys.stderr)
        return 1
    try:
        store, embedder = backends(args)
        report = ingest(args.manifest, args.root, store, embedder)
    except EmbedStackMissing as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SystemExit:
        raise
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {str(exc).splitlines()[0][:200] if str(exc) else ''}", file=sys.stderr)
        return 1

    for d in report.documents:
        line = f"{d.status:<9} {d.document_id}"
        if d.status in ("ingested", "unchanged"):
            line += f"  v{d.version}  chunks={d.chunks}"
        if d.status == "ingested":
            line += f"  sections={d.sections}  pages={d.pages if d.pages is not None else '-'}"
            line += f"  truncated={d.truncated_chunks if d.truncated_chunks is not None else 'n/a'}"
            if d.empty_pages:
                line += f"  empty_pages={len(d.empty_pages)}"
        if d.reason:
            line += f"  ({d.reason})"
        print(line)
    path = write_report(report, args.report_dir)
    print(f"run record: {path.name}")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
