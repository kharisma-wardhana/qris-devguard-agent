"""Qdrant collection access for the knowledge base (p2 spec Data; AC #3, #4).

Collection schema (pinned by tests; changing it needs a regression test, CLAUDE.md):
  dense  - 1024-d cosine (BGE-M3)
  bm25   - sparse, Qdrant applies IDF (`modifier: idf`)
A document is identified by (document_id, document_version), so two versions coexist (§12.5).
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client import models as qm

from app.rag import config

DENSE, SPARSE = "dense", "bm25"


def make_client(local_path: Path | None = None) -> QdrantClient:
    """Server client from config, or an on-disk local-mode client for tests."""
    if local_path is not None:
        return QdrantClient(path=str(local_path))
    return QdrantClient(url=config.qdrant_url(), timeout=60)


def _doc_filter(document_id: str, version: str | None = None) -> qm.Filter:
    must = [qm.FieldCondition(key="document_id", match=qm.MatchValue(value=document_id))]
    if version is not None:
        must.append(qm.FieldCondition(key="document_version", match=qm.MatchValue(value=version)))
    return qm.Filter(must=must)


class Store:
    def __init__(self, client: QdrantClient, collection: str | None = None) -> None:
        self.client = client
        self.collection = collection or config.retrieval()["collection"]

    def ensure_collection(self, dimension: int) -> None:
        if self.client.collection_exists(self.collection):
            info = self.client.get_collection(self.collection)
            size = info.config.params.vectors[DENSE].size
            if size != dimension:
                raise RuntimeError(f"collection {self.collection} has dense size {size}, expected {dimension}")
            return
        self.client.create_collection(
            self.collection,
            vectors_config={DENSE: qm.VectorParams(size=dimension, distance=qm.Distance.COSINE)},
            sparse_vectors_config={SPARSE: qm.SparseVectorParams(modifier=qm.Modifier.IDF)},
        )
        with warnings.catch_warnings():      # local mode warns that payload indexes do nothing there
            warnings.simplefilter("ignore", UserWarning)
            for field in ("document_id", "document_version", "section_id"):
                self.client.create_payload_index(self.collection, field, qm.PayloadSchemaType.KEYWORD)

    def document_state(self, document_id: str, version: str) -> tuple[str | None, str | None]:
        """(source checksum, pipeline fingerprint) of a stored document, or (None, None)."""
        points, _ = self.client.scroll(self.collection, scroll_filter=_doc_filter(document_id, version), limit=1,
                                       with_payload=["checksum_sha256", "pipeline_fingerprint"], with_vectors=False)
        if not points:
            return None, None
        return points[0].payload.get("checksum_sha256"), points[0].payload.get("pipeline_fingerprint")

    def delete_document(self, document_id: str, version: str) -> None:
        self.client.delete(self.collection, points_selector=qm.FilterSelector(filter=_doc_filter(document_id, version)))

    def upsert(self, points: list[qm.PointStruct]) -> int:
        if points:
            self.client.upsert(self.collection, points=points, wait=True)
        return len(points)

    def count(self, document_id: str | None = None, version: str | None = None) -> int:
        flt = _doc_filter(document_id, version) if document_id else None
        return self.client.count(self.collection, count_filter=flt, exact=True).count

    def payloads(self) -> list[dict[str, Any]]:
        out, offset = [], None
        while True:
            points, offset = self.client.scroll(self.collection, limit=256, offset=offset,
                                                with_payload=True, with_vectors=False)
            out.extend(p.payload for p in points)
            if offset is None:
                return out
