"""Helpers for scripts/setup.sh (p9-final spec Behaviour §1; AC #1-#6). Bash never parses YAML; it calls these.

  python -m app.evaluation.setup_checks pdfs [--root R]          # reference PDFs present with the manifest checksum
  python -m app.evaluation.setup_checks wait [--timeout S] [--port P ...]   # compose ports accept connections
  python -m app.evaluation.setup_checks synthetic-manifest --out F          # manifest with team_authored entries only

`pdfs` makes no network request: a missing file is reported with its portal page (`source_url`), file name and
`local_path` for the developer to download by hand; a checksum mismatch names the entry. Exit 0 when every
approved `reference_only` file is present and matches, 1 otherwise.
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from app.evaluation import ROOT

MANIFEST = Path("knowledge_base/manifests/sources.yaml")
WAIT_SERVICES = ("postgres", "qdrant")


def _entries(root: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load((root / MANIFEST).read_text(encoding="utf-8")) or {}
    return list(data.get("sources") or [])


def reference_pdfs(root: Path) -> list[str]:
    """One line per problem with an approved reference_only file; empty means all present and matching."""
    from app.rag.manifest import sha256_file

    problems = []
    for e in _entries(root):
        if e.get("approval_status") != "approved" or (e.get("permission") or {}).get("basis") != "reference_only":
            continue
        rel = str(e.get("local_path") or "")
        path = root / rel
        if not path.is_file():
            problems.append(f"MISSING {e['document_id']}: download '{Path(rel).name}' (version {e.get('version')}) "
                            f"from {e.get('source_url')} and save it as {rel}")
            continue
        expected = str(e.get("checksum_sha256") or "").lower()
        if expected and sha256_file(path) != expected:
            problems.append(f"CHECKSUM {e['document_id']}: {rel} does not match the manifest checksum; it is a "
                            "different version. Replace it with the version the manifest names; do not edit the manifest.")
    return problems


def compose_ports(root: Path, services: tuple[str, ...] = WAIT_SERVICES) -> list[int]:
    compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
    ports = []
    for name in services:
        for mapping in (compose.get("services", {}).get(name, {}) or {}).get("ports", []) or []:
            parts = str(mapping).split(":")
            if len(parts) >= 2:
                ports.append(int(parts[-2]))
    return ports


def wait_for(ports: list[int], timeout: float, host: str = "127.0.0.1") -> list[int]:
    """Ports still not accepting connections when `timeout` runs out (short per-attempt timeouts: CLAUDE.md)."""
    deadline = time.monotonic() + timeout
    pending = list(ports)
    while pending:
        still = []
        for port in pending:
            try:
                with socket.create_connection((host, port), timeout=1.5):
                    pass
            except OSError:
                still.append(port)
        pending = still
        if not pending or time.monotonic() >= deadline:
            break
        time.sleep(1)
    return pending


def synthetic_manifest(root: Path, out: Path) -> int:
    data = yaml.safe_load((root / MANIFEST).read_text(encoding="utf-8")) or {}
    kept = [e for e in data.get("sources") or [] if (e.get("permission") or {}).get("basis") == "team_authored"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump({**data, "sources": kept}, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return len(kept)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="setup_checks")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pdfs")
    p.add_argument("--root", type=Path, default=ROOT)
    w = sub.add_parser("wait")
    w.add_argument("--root", type=Path, default=ROOT)
    w.add_argument("--timeout", type=float, default=120)
    w.add_argument("--port", type=int, action="append", default=None)
    s = sub.add_parser("synthetic-manifest")
    s.add_argument("--root", type=Path, default=ROOT)
    s.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    if args.cmd == "pdfs":
        problems = reference_pdfs(args.root)
        for line in problems:
            print(line)
        return 1 if problems else 0
    if args.cmd == "wait":
        ports = args.port or compose_ports(args.root)
        left = wait_for(ports, args.timeout)
        if left:
            print(f"not accepting connections after {args.timeout:.0f} s: port(s) {', '.join(map(str, left))}")
            return 1
        return 0
    n = synthetic_manifest(args.root, args.out)
    print(f"{n} team_authored source(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
