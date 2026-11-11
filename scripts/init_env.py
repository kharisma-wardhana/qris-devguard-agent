"""Create .env from .env.example, replacing every `replace-me` with a random secret.

Usage: uv run python scripts/init_env.py [--example .env.example] [--out .env] [--add-missing]

- Refuses to overwrite an existing .env (delete it yourself if you mean to rotate everything).
- `--add-missing` (p7): appends only the keys of .env.example that an existing .env lacks (new secrets get
  random values), leaving every existing line byte-identical. Prints key names only.
- Never prints a generated value.
- The password inside DATABASE_URL is set to the generated POSTGRES_PASSWORD.
- `set-me` values (SSH host and port) are left for you to fill in.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER = "replace-me"


def generate(key: str) -> str:
    if key == "LANGFUSE_PUBLIC_KEY":
        return "pk-lf-" + secrets.token_hex(16)
    if key == "LANGFUSE_SECRET_KEY":
        return "sk-lf-" + secrets.token_hex(16)
    if key == "ENCRYPTION_KEY":
        return secrets.token_hex(32)  # Langfuse requires 64 hex characters
    return secrets.token_hex(24)  # hex only: safe inside URLs and shell commands


def render(example: str) -> tuple[str, list[str]]:
    values: dict[str, str] = {}
    lines, filled = [], []
    for line in example.splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#") and value.strip() == PLACEHOLDER:
            values[key] = generate(key)
            line = f"{key}={values[key]}"
            filled.append(key)
        lines.append(line)
    text = "\n".join(lines) + "\n"
    if "POSTGRES_PASSWORD" in values:
        text = text.replace(f"devguard:{PLACEHOLDER}@", f"devguard:{values['POSTGRES_PASSWORD']}@")
        filled.append("DATABASE_URL")
    return text, filled


def keys_of(text: str) -> set[str]:
    out = set()
    for line in text.splitlines():
        key, sep, _ = line.partition("=")
        if sep and not line.lstrip().startswith("#"):
            out.add(key.strip())
    return out


def missing_lines(example: str, existing: str) -> tuple[list[str], list[str]]:
    """(lines to append, generated key names) for keys in `example` that `existing` lacks."""
    have = keys_of(existing)
    lines, filled = [], []
    for line in example.splitlines():
        key, sep, value = line.partition("=")
        if not sep or line.lstrip().startswith("#") or key.strip() in have:
            continue
        if value.strip() == PLACEHOLDER:
            line = f"{key}={generate(key)}"
            filled.append(key)
        lines.append(line)
    return lines, filled


def add_missing(example_path: Path, out: Path) -> int:
    existing = out.read_bytes().decode("utf-8")
    lines, filled = missing_lines(example_path.read_text(encoding="utf-8"), existing)
    if not lines:
        print(f"{out.name} already has every key; nothing added")
        return 0
    newline = "\r\n" if "\r\n" in existing else "\n"
    block = "" if not existing or existing.endswith(("\n", "\r")) else newline
    block += newline.join(["# added by init_env.py --add-missing", *lines]) + newline
    with out.open("a", encoding="utf-8", newline="") as fh:            # append only: existing bytes untouched
        fh.write(block)
    added = [ln.partition("=")[0] for ln in lines]
    print(f"Added {len(added)} keys to {out.name}: {', '.join(added)} ({len(filled)} generated)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--example", type=Path, default=ROOT / ".env.example")
    parser.add_argument("--out", type=Path, default=ROOT / ".env")
    parser.add_argument("--add-missing", action="store_true",
                        help="append only the keys an existing .env lacks; never change an existing line")
    args = parser.parse_args(argv)

    if args.add_missing and args.out.exists():
        return add_missing(args.example, args.out)
    if args.out.exists():
        print(f"{args.out.name} already exists; not overwriting. Delete it first to regenerate.", file=sys.stderr)
        return 1
    text, filled = render(args.example.read_text(encoding="utf-8"))
    args.out.write_text(text, encoding="utf-8")
    print(f"Wrote {args.out.name} with {len(filled)} generated values: {', '.join(filled)}")
    print("Next: copy VLLM_API_KEY into a RunPod secret, and fill in RUNPOD_SSH_HOST / RUNPOD_SSH_PORT per pod.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
