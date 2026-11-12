"""False-positive check for answer-echo fencing (p8 spec AC #24).

Runs `injection_markers` (configs/security.yaml) over every retrievable chunk and prints, per document version,
how many chunks hold a sentence the fence would remove and how many such sentences per marker category.
It prints document IDs, versions and counts only: never chunk text.

  uv run python scripts/scan_markers.py              # the local Qdrant collection, retrievable documents only
  uv run python scripts/scan_markers.py --harness    # the CI harness corpus (in memory, no services)

The whole chunk is scanned, not only the excerpt a search returns, so the count is an upper bound on what
fencing can remove from the prompt.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def scan(payloads: list[dict], allowed: set[str] | None) -> dict[tuple[str, str], dict]:
    from app.agent import fence
    out: dict[tuple[str, str], dict] = {}
    for p in payloads:
        if allowed is not None and p["document_id"] not in allowed:
            continue
        row = out.setdefault((p["document_id"], str(p["document_version"])),
                             {"chunks": 0, "matched_chunks": 0, "sentences": 0, "categories": defaultdict(int)})
        row["chunks"] += 1
        parts = fence._sentences(p.get("text") or "")
        hits = [fence.categories(s) for s in parts[0::2] if s.strip()]
        hits = [h for h in hits if h]
        if hits:
            row["matched_chunks"] += 1
            row["sentences"] += len(hits)
            for h in hits:
                for name in h:
                    row["categories"][name] += 1
    return out


def harness_payloads(plant: list[Path]) -> list[dict]:
    from app.agent.harness import build_corpus
    with tempfile.TemporaryDirectory(prefix="devguard-scan-") as tmp:
        store, _ = build_corpus(Path(tmp), plant or None)
        return store.payloads()


def qdrant_payloads() -> tuple[list[dict], set[str]]:
    from app.rag import config as rag_config
    from app.rag.manifest import load_manifest, retrievable_document_ids
    from app.rag.store import Store, make_client
    from app.security.egress import check
    check("QDRANT_URL", rag_config.qdrant_url())
    store = Store(make_client())
    if not store.client.collection_exists(store.collection):
        raise RuntimeError("the collection does not exist; run scripts/ingest.py first")
    allowed = retrievable_document_ids(load_manifest(ROOT / "knowledge_base/manifests/sources.yaml"))
    return store.payloads(), allowed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Count injection-marker matches per retrievable document.")
    parser.add_argument("--harness", action="store_true", help="scan the CI harness corpus (no services)")
    parser.add_argument("--plant", type=Path, action="append", default=[], help=argparse.SUPPRESS)  # tests only
    args = parser.parse_args(argv)
    if args.plant and not args.harness:
        print("error: --plant needs --harness", file=sys.stderr)
        return 2
    try:
        if args.harness:
            payloads, allowed, source = harness_payloads(args.plant), None, "harness corpus"
        else:
            (payloads, allowed), source = qdrant_payloads(), "local Qdrant, retrievable documents"
    except Exception as exc:  # noqa: BLE001 - one line, the type only: a message could name a URL
        print(f"error: could not read the chunks ({type(exc).__name__}); is Qdrant up and ingested?", file=sys.stderr)
        return 1
    rows = scan(payloads, allowed)
    print(f"injection-marker scan over the {source}")
    total = {"chunks": 0, "matched_chunks": 0, "sentences": 0}
    for (doc, version), row in sorted(rows.items()):
        cats = ",".join(f"{k}:{v}" for k, v in sorted(row["categories"].items())) or "-"
        print(f"{doc} {version} chunks={row['chunks']} matched_chunks={row['matched_chunks']} "
              f"sentences={row['sentences']} categories={cats}")
        for k in total:
            total[k] += row[k]
    print(f"total documents={len(rows)} chunks={total['chunks']} matched_chunks={total['matched_chunks']} "
          f"sentences={total['sentences']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
