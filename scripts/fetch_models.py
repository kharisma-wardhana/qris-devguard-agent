"""Download the pinned embedding model into the local Hugging Face cache (one-time setup).

Usage:
  uv sync --extra embed
  uv run python scripts/fetch_models.py            # downloads ~2.3 GB once
  uv run python scripts/fetch_models.py --dry-run  # prints what would be fetched; no network

Uses `truststore`, so TLS is verified against the operating system's certificate store
(needed on networks that inspect TLS). Verification is never disabled. Ingestion and
search run offline afterwards (HF_HUB_OFFLINE=1).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag import config  # noqa: E402

# Files sentence-transformers needs for BGE-M3 dense; skips ONNX, images, ColBERT/sparse heads.
ALLOW = ["*.json", "*.txt", "*.model", "pytorch_model.bin", "1_Pooling/*"]
IGNORE = ["onnx/*", "imgs/*", "colbert_linear.pt", "sparse_linear.pt"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch the pinned embedding model.")
    parser.add_argument("--dry-run", action="store_true", help="print the plan; no network")
    args = parser.parse_args(argv)
    cfg = config.retrieval()["embedding"]
    print(f"model: {cfg['model_id']}  revision: {cfg['revision']}")
    print(f"files: {', '.join(ALLOW)}  (skipping {', '.join(IGNORE)})")
    if args.dry_run:
        print("dry run: nothing downloaded")
        return 0
    try:
        import truststore
        from huggingface_hub import snapshot_download
    except ImportError:
        print("error: embedding stack not installed: run `uv sync --extra embed`", file=sys.stderr)
        return 2
    truststore.inject_into_ssl()
    try:
        path = Path(snapshot_download(cfg["model_id"], revision=cfg["revision"],
                                      allow_patterns=ALLOW, ignore_patterns=IGNORE))
    except Exception as exc:                       # network/auth/not-found: one line, no traceback
        print(f"error: download failed: {type(exc).__name__}: {str(exc).splitlines()[0][:200]}", file=sys.stderr)
        return 1
    files = [p for p in path.rglob("*") if p.is_file()]
    size = sum(p.stat().st_size for p in files)
    print(f"cached: {len(files)} files, {size / 1e9:.2f} GB at revision {cfg['revision'][:7]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
