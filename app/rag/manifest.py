"""Selects ingestible sources from the manifest and checks each file (p2 spec Behaviour 2.1-2.2, AC #1).

Only `approved` entries with a `local_path` are considered. A file must sit in the storage
area its permission basis requires, have an allowed type whose content matches its extension,
be within the size limit, and match its manifest checksum when one is recorded.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from app.rag import config

STORAGE_AREA = {
    "reference_only": PurePosixPath("knowledge_base/sources"),
    "team_authored": PurePosixPath("knowledge_base/synthetic"),
}

OK, SKIPPED, FAILED = "ok", "skipped", "failed"


@dataclass(frozen=True)
class SourceCheck:
    document_id: str
    status: str                 # ok | skipped | failed
    reason: str = ""
    entry: dict[str, Any] | None = None
    path: Path | None = None
    sha256: str | None = None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _content_matches(path: Path, suffix: str) -> bool:
    with path.open("rb") as fh:
        head = fh.read(8)
    if suffix == ".pdf":
        return head.startswith(b"%PDF-")
    if suffix == ".md":
        if head.startswith(b"%PDF-"):
            return False
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return False
        return True
    return False


def check_source(entry: dict[str, Any], root: Path) -> SourceCheck:
    doc_id = entry.get("document_id", "<missing id>")

    def result(status: str, reason: str = "", **kw: Any) -> SourceCheck:
        return SourceCheck(doc_id, status, reason, entry, **kw)

    if entry.get("approval_status") != "approved":
        return result(SKIPPED, f"not approved ({entry.get('approval_status')})")
    local = entry.get("local_path")
    if not local:
        return result(SKIPPED, "no local_path")
    basis = (entry.get("permission") or {}).get("basis")
    area = STORAGE_AREA.get(basis)
    rel = PurePosixPath(str(local).replace("\\", "/"))
    if area is None or rel.is_absolute() or ".." in rel.parts or rel.parent != area:
        return result(SKIPPED, f"wrong storage area for {basis}: {local}")
    ingest_cfg = config.retrieval()["ingest"]
    suffix = rel.suffix.lower()
    if suffix not in ingest_cfg["types"]:
        return result(SKIPPED, f"type not allowed: {suffix or '<none>'}")
    path = root / Path(*rel.parts)
    if not path.is_file():
        return result(FAILED, f"file not found: {local}", path=path)
    if path.stat().st_size > ingest_cfg["max_file_mb"] * 1024 * 1024:
        return result(SKIPPED, f"file larger than {ingest_cfg['max_file_mb']} MB", path=path)
    if not _content_matches(path, suffix):
        return result(SKIPPED, f"content does not match {suffix}", path=path)
    digest = sha256_file(path)
    expected = entry.get("checksum_sha256")
    if expected and str(expected).lower() != digest:
        return result(FAILED, "checksum mismatch with manifest", path=path, sha256=digest)
    return result(OK, path=path, sha256=digest)


def load_manifest(manifest_path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    return list(data.get("sources") or [])


def select_sources(manifest_path: Path, root: Path) -> list[SourceCheck]:
    return [check_source(entry, root) for entry in load_manifest(manifest_path)]
