"""Generate test-only signature keys (RSA-2048 pairs, 64-byte HMAC secrets) outside version control.

Usage:
  uv run python scripts/gen_test_keys.py                # into the git-ignored key dir (configs/mcp.yaml)
  uv run python scripts/gen_test_keys.py --out <dir>    # e.g. a temp dir in tests/CI
  uv run python scripts/gen_test_keys.py --force        # regenerate every key

The key IDs come from knowledge_base/fixtures/signatures/cases.yaml. Existing keys are kept.
Never prints key material. These keys are synthetic test material, never production keys.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CASES = ROOT / "knowledge_base" / "fixtures" / "signatures" / "cases.yaml"


def main(argv: list[str] | None = None) -> int:
    from app.mcp_server import config
    from app.validators import keys

    parser = argparse.ArgumentParser(description="Generate test-only signature keys.")
    parser.add_argument("--out", type=Path, default=None, help="key directory (default: configs/mcp.yaml keys_dir)")
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--force", action="store_true", help="regenerate every key")
    args = parser.parse_args(argv)

    wanted = yaml.safe_load(args.cases.read_text(encoding="utf-8"))["keys"]
    out = args.out or config.keys_dir()
    written = keys.generate(out, wanted, force=args.force)
    kept = sorted(set(wanted) - set(written))
    print(f"test keys in {out}: wrote {len(written)} ({', '.join(written) or '-'}), kept {len(kept)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
