"""Extract the SNAP response-code table (part 6) from the local, reference-only PDF.

Usage:
  uv run python scripts/extract_response_codes.py

Writes knowledge_base/sources/derived/snap-response-codes-1.0.2.json, which is git-ignored with the
rest of knowledge_base/sources/ and must never be committed (reference-only licence, T16).
Refuses to run unless the PDF's SHA-256 matches its manifest entry.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MANIFEST = ROOT / "knowledge_base" / "manifests" / "sources.yaml"


def main(argv: list[str] | None = None) -> int:
    from app.mcp_server import config
    from app.rag.manifest import sha256_file
    from app.validators.response_codes import parse_pdf_rows

    v = config.mcp()["validators"]
    ext = v["extract"]
    meta = v["response_code_tables"]["snap"]
    version = next(iter(meta))
    parser = argparse.ArgumentParser(description="Extract the SNAP response-code table from the local PDF.")
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--root", type=Path, default=ROOT, help="repo root that local_path is relative to")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    entries = yaml.safe_load(args.manifest.read_text(encoding="utf-8"))["sources"]
    entry = next((s for s in entries if s["document_id"] == ext["document_id"]), None)
    if entry is None or entry.get("approval_status") != "approved" or not entry.get("local_path"):
        print(f"error: {ext['document_id']} is not an approved manifest entry with a local_path", file=sys.stderr)
        return 1
    if str(entry["document_version"]) != version:
        print(f"error: manifest version {entry['document_version']} != configured table version {version}", file=sys.stderr)
        return 1
    pdf = args.root / Path(*entry["local_path"].split("/"))
    if not pdf.is_file():
        print(f"error: PDF not found: {entry['local_path']} (reference-only, kept locally)", file=sys.stderr)
        return 1
    digest = sha256_file(pdf)
    if digest != str(entry.get("checksum_sha256") or "").lower():
        print(f"error: checksum mismatch for {entry['local_path']}; refusing to extract", file=sys.stderr)
        return 1

    import pdfplumber

    rows = []
    with pdfplumber.open(pdf) as doc:
        for page_no in range(ext["first_page"], ext["last_page"] + 1):
            for table in doc.pages[page_no - 1].extract_tables():
                rows.extend((page_no, r) for r in table)
    parsed = parse_pdf_rows(rows)
    if not parsed:
        print("error: no response-code rows found on the configured pages", file=sys.stderr)
        return 1
    out = args.out or (args.root / Path(*meta[version]["path"].split("/")))
    out.parent.mkdir(parents=True, exist_ok=True)
    table = {"document_id": entry["document_id"], "document_version": version, "synthetic": False,
             "source_checksum_sha256": digest, "pages": [ext["first_page"], ext["last_page"]],
             "note": "Derived from a reference-only document. Never commit.", "entries": parsed}
    out.write_text(json.dumps(table, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    http = sorted({e["http_status"] for e in parsed})
    print(f"wrote {len(parsed)} entries to {out} (HTTP statuses: {', '.join(http)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
