"""Ingestion pipeline (p2 spec Behaviour 2; AC #1, #3, #4).

manifest -> file checks -> extract -> chunk -> dense + sparse vectors -> Qdrant.
A document whose checksum is unchanged is left alone; a changed one is replaced whole.
The run record holds IDs, counts and hashes only, never document text.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from qdrant_client import models as qm

from app.rag import chunking, config, extract, manifest, sparse
from app.rag.embedding import Embedder
from app.rag.store import DENSE, SPARSE, Store

SYNTHETIC_MARKER = "(SYNTHETIC — not an official ASPI/BI document)"
INGESTED, UNCHANGED = "ingested", "unchanged"


@dataclass
class DocResult:
    document_id: str
    status: str                      # ingested | unchanged | skipped | failed
    reason: str = ""
    version: str | None = None
    chunks: int = 0
    sections: int = 0
    pages: int | None = None
    empty_pages: list[int] = field(default_factory=list)
    truncated_chunks: int | None = None
    upserted: int = 0
    avg_terms: float | None = None
    checksum_sha256: str | None = None


@dataclass
class IngestReport:
    started_at: str
    collection: str
    documents: list[DocResult]

    @property
    def failed(self) -> bool:
        return any(d.status == manifest.FAILED for d in self.documents)


def pipeline_fingerprint() -> str:
    """Hash of every setting that changes chunks or vectors; a change forces re-ingestion."""
    cfg = config.retrieval()
    relevant = {"chunking": cfg["chunking"], "sparse": cfg["sparse"], "embedding": cfg["embedding"]}
    return hashlib.sha256(json.dumps(relevant, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _payload(entry: dict[str, Any], chunk: chunking.Chunk, sha: str, now: str) -> dict[str, Any]:
    effective = entry.get("effective_date")
    return {
        "chunk_id": chunk.chunk_id,
        "document_id": entry["document_id"],
        "title": entry["title"],
        "publisher": entry.get("publisher"),
        "source_url": entry.get("source_url"),
        "document_version": str(entry["document_version"]),
        "effective_date": None if effective is None else str(effective),
        "language": list(entry.get("language") or []),
        "permission_basis": entry["permission"]["basis"],
        "approval_status": entry["approval_status"],
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "section_id": chunk.section_id,
        "section_title": chunk.section_title,
        "checksum_sha256": sha,
        "pipeline_fingerprint": pipeline_fingerprint(),
        "text_hash": chunk.text_hash,
        "ingested_at": now,
        "text": chunk.text,
    }


def ingest_document(check: manifest.SourceCheck, store: Store, embedder: Embedder, now: str) -> DocResult:
    entry = check.entry or {}
    version = str(entry.get("document_version"))
    result = DocResult(check.document_id, INGESTED, version=version, checksum_sha256=check.sha256)
    if entry.get("permission", {}).get("basis") == "team_authored" and SYNTHETIC_MARKER not in entry.get("title", ""):
        result.status, result.reason = manifest.FAILED, "synthetic title marker missing"
        return result
    if store.document_state(check.document_id, version) == (check.sha256, pipeline_fingerprint()):
        result.status = UNCHANGED
        result.chunks = store.count(check.document_id, version)
        return result

    ext = extract.extract(check.path)
    chunks = chunking.chunk_document(ext.sections, check.document_id, version)
    if not chunks:
        result.status, result.reason = manifest.FAILED, "no text extracted"
        return result
    texts = [c.text for c in chunks]
    dense = embedder.embed_documents(texts)
    max_seq = config.retrieval()["embedding"]["max_seq_length"]
    counts = [embedder.count_tokens(t) for t in texts]
    points, terms = [], []
    for chunk, vec in zip(chunks, dense):
        sp = sparse.encode_document(chunk.text)
        terms.append(len(sparse.tokenize(chunk.text)))
        points.append(qm.PointStruct(
            id=chunk.chunk_id,
            vector={DENSE: vec, SPARSE: qm.SparseVector(indices=sp.indices, values=sp.values)},
            payload=_payload(entry, chunk, check.sha256, now),
        ))
    store.delete_document(check.document_id, version)       # replace whole document on change
    result.upserted = store.upsert(points)
    result.chunks = len(chunks)
    result.sections = len({c.section_id for c in chunks})
    result.pages = ext.page_count
    result.empty_pages = ext.empty_pages
    result.truncated_chunks = None if counts[0] is None else sum(n > max_seq for n in counts if n is not None)
    result.avg_terms = round(sum(terms) / len(terms), 1)
    return result


def ingest(manifest_path: Path, root: Path, store: Store, embedder: Embedder) -> IngestReport:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    store.ensure_collection(embedder.dimension)
    results = []
    for check in manifest.select_sources(manifest_path, root):
        if check.status != manifest.OK:
            results.append(DocResult(check.document_id, check.status, check.reason,
                                     version=None if not check.entry else str(check.entry.get("document_version")),
                                     checksum_sha256=check.sha256))
            continue
        try:
            results.append(ingest_document(check, store, embedder, now))
        except Exception as exc:         # one bad document must not hide the others' results
            results.append(DocResult(check.document_id, manifest.FAILED,
                                     f"{type(exc).__name__}: {str(exc).splitlines()[0][:160] if str(exc) else ''}"))
    return IngestReport(now, store.collection, results)


def write_report(report: IngestReport, report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"ingest-{report.started_at.replace(':', '').replace('+', 'Z')[:17]}.json"
    path.write_text(json.dumps({"started_at": report.started_at, "collection": report.collection,
                                "documents": [asdict(d) for d in report.documents]}, indent=2), encoding="utf-8")
    return path
