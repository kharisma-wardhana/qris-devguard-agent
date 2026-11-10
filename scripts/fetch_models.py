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

# Per model: the config block it is pinned in, and the files sentence-transformers needs.
MODELS = [
    # BGE-M3 dense (p2): skips ONNX, images and the ColBERT/sparse heads.
    ("embedding", ["*.json", "*.txt", "*.model", "pytorch_model.bin", "1_Pooling/*"],
     ["onnx/*", "imgs/*", "colbert_linear.pt", "sparse_linear.pt"]),
    # BGE reranker v2 M3 cross-encoder (p3): safetensors weights + tokenizer only.
    ("rerank", ["*.json", "*.model", "model.safetensors"], []),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch the pinned embedding and reranker models.")
    parser.add_argument("--dry-run", action="store_true", help="print the plan; no network")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    plan = [(config.retrieval()[block], allow, ignore) for block, allow, ignore in MODELS]
    for cfg, allow, ignore in plan:
        print(f"model: {cfg['model_id']}  revision: {cfg['revision']}")
        print(f"  files: {', '.join(allow)}" + (f"  (skipping {', '.join(ignore)})" if ignore else ""))
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
    for cfg, allow, ignore in plan:
        try:
            path = Path(snapshot_download(cfg["model_id"], revision=cfg["revision"],
                                          allow_patterns=allow, ignore_patterns=ignore or None))
        except Exception as exc:                   # network/auth/not-found: one line, no traceback
            print(f"error: download of {cfg['model_id']} failed: {type(exc).__name__}: "
                  f"{str(exc).splitlines()[0][:200] if str(exc) else ''}", file=sys.stderr)
            return 1
        files = [p for p in path.rglob("*") if p.is_file()]
        size = sum(p.stat().st_size for p in files)
        print(f"cached: {cfg['model_id']}: {len(files)} files, {size / 1e9:.2f} GB at revision {cfg['revision'][:7]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
