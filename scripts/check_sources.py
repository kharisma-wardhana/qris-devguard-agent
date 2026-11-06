"""Validate the source inventory manifest (Phase P0 exit check).

Usage: uv run python scripts/check_sources.py [path/to/sources.yaml]
Exits non-zero and prints one line per problem if the manifest is invalid.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_DIR = ROOT / "knowledge_base" / "manifests"
DEFAULT_MANIFEST = MANIFEST_DIR / "sources.yaml"
SCHEMA_PATH = MANIFEST_DIR / "source.schema.json"

PLACEHOLDER_HOSTS = {"example.org", "example.com", "example.net", "approved-source.example"}

# Git-ignored directory for public documents that may not be redistributed.
IGNORED_SOURCES_DIR = "knowledge_base/sources/"
SYNTHETIC_PREFIX = "synthetic-"
SYNTHETIC_MARKER = "(SYNTHETIC — not an official ASPI/BI document)"


def _is_placeholder(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in PLACEHOLDER_HOSTS or host.endswith(".example")


def _check_local_path(s: dict, root: Path) -> list[str]:
    doc, path = s["document_id"], s.get("local_path")
    basis = s["permission"]["basis"]
    if not path:
        if basis == "team_authored" and s["approval_status"] == "approved":
            return [f"{doc}: approved team_authored source requires local_path"]
        return []
    if ".." in Path(path).parts:
        return [f"{doc}: local_path must not contain '..'"]
    in_ignored = path.startswith(IGNORED_SOURCES_DIR)
    if basis == "team_authored":
        if in_ignored:
            return [f"{doc}: team_authored local_path must be committed, not under {IGNORED_SOURCES_DIR}"]
        if s["approval_status"] == "approved" and not (root / path).is_file():
            return [f"{doc}: local_path '{path}' does not exist"]
    if basis == "reference_only" and not in_ignored:
        return [f"{doc}: reference_only local_path must be under {IGNORED_SOURCES_DIR} (never committed)"]
    return []


def check_manifest(manifest: dict, schema: dict, root: Path = ROOT) -> list[str]:
    """Return a list of human-readable errors; empty means the manifest is valid."""
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = [
        f"schema: {'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in sorted(validator.iter_errors(manifest), key=lambda e: list(e.absolute_path))
    ]
    if errors:
        return errors

    sources = manifest["sources"]
    ids = [s["document_id"] for s in sources]
    known = set(ids)
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        errors.append(f"{dup}: duplicate document_id")

    for s in sources:
        doc = s["document_id"]
        url = s["source_url"]
        basis = s["permission"]["basis"]
        if url and _is_placeholder(url):
            errors.append(f"{doc}: source_url is a placeholder ({url})")

        is_synthetic = doc.startswith(SYNTHETIC_PREFIX)
        if is_synthetic and basis != "team_authored":
            errors.append(f"{doc}: synthetic source requires permission.basis team_authored")
        if basis == "team_authored" and not is_synthetic:
            errors.append(f"{doc}: team_authored source requires document_id prefix '{SYNTHETIC_PREFIX}'")
        if is_synthetic and SYNTHETIC_MARKER not in s["title"]:
            errors.append(f"{doc}: synthetic source title must contain '{SYNTHETIC_MARKER}'")

        errors.extend(_check_local_path(s, root))

        if s["approval_status"] == "approved":
            if not url and basis != "team_authored":
                errors.append(f"{doc}: approved source requires source_url")
            if not s["document_version"]:
                errors.append(f"{doc}: approved source requires document_version")
            if not s["effective_date"]:
                errors.append(f"{doc}: approved source requires effective_date")
            if s["permission"]["basis"] == "unconfirmed":
                errors.append(f"{doc}: approved source requires confirmed permission.basis")
            if not s["permission"]["evidence"]:
                errors.append(f"{doc}: approved source requires permission.evidence")

        if s["approval_status"] == "superseded":
            target = s.get("superseded_by")
            if not target:
                errors.append(f"{doc}: superseded source requires superseded_by")
            elif target not in known:
                errors.append(f"{doc}: superseded_by '{target}' is not in the manifest")

    return errors


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_MANIFEST
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = check_manifest(manifest, schema)
    if errors:
        print("\n".join(errors))
        return 1

    sources = manifest["sources"]
    approved = sum(s["approval_status"] == "approved" for s in sources)
    print(f"OK: {len(sources)} sources, {approved} approved for ingestion")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
