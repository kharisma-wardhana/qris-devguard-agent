"""Hybrid search with citation-ready results (p2 spec Behaviour 3; AC #6, #7, #9).

One Qdrant `query_points` call: a dense prefetch and a sparse (BM25) prefetch fused
with RRF. `dense` and `sparse` modes exist for the retrieval eval.

Every result carries the TRD §12.4 citation fields, a run-scoped citation ID and an
excerpt capped by permission basis (configs/security.yaml). Result text is untrusted
data: callers must never treat it as instructions (TRD invariant; enforced in P5).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from qdrant_client import models as qm

from app.rag import config, sparse
from app.rag.embedding import Embedder
from app.rag.store import DENSE, SPARSE, Store

MODES = ("hybrid", "dense", "sparse")
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class Citation:
    citation_id: str
    document_id: str
    document_version: str
    title: str
    page: int | None
    section: str
    chunk_id: str
    source_url: str | None
    quote_or_excerpt: str
    # beyond §12.4: what P3-P5 need to rank, label and validate
    rank: int
    score: float
    section_id: str
    page_end: int | None
    permission_basis: str
    approval_status: str
    untrusted: bool = True

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class SearchRun:
    run_id: str
    query: str
    mode: str
    results: list[Citation]
    chunk_text: dict[str, str] = field(default_factory=dict, repr=False)   # chunk_id -> text, in memory only


def normalise_query(query: str) -> str:
    """Collapse whitespace only: identifiers, codes, header names and algorithm names stay intact."""
    return _WS.sub(" ", query).strip()


def excerpt(text: str, permission_basis: str) -> str:
    """A literal substring of the chunk body, at most the licence limit, ending at a word boundary."""
    body = text.split("\n\n", 1)[1] if "\n\n" in text else text
    limit = config.excerpt_limit(permission_basis)
    if len(body) <= limit:
        return body
    cut = body[:limit]
    space = cut.rfind(" ")
    return cut[:space] if space > limit // 2 else cut


def _points(store: Store, embedder: Embedder, query: str, mode: str, limit: int):
    cfg = config.retrieval()["search"]
    if mode == "dense":
        return store.client.query_points(store.collection, query=embedder.embed_query(query), using=DENSE,
                                         limit=limit, with_payload=True).points
    sq = sparse.encode_query(query)
    sparse_vec = qm.SparseVector(indices=sq.indices, values=sq.values)
    if mode == "sparse":
        if not sq.indices:
            return []
        return store.client.query_points(store.collection, query=sparse_vec, using=SPARSE,
                                         limit=limit, with_payload=True).points
    prefetch = [qm.Prefetch(query=embedder.embed_query(query), using=DENSE, limit=cfg["dense_prefetch"])]
    if sq.indices:
        prefetch.append(qm.Prefetch(query=sparse_vec, using=SPARSE, limit=cfg["sparse_prefetch"]))
    return store.client.query_points(store.collection, prefetch=prefetch, query=qm.FusionQuery(fusion=qm.Fusion.RRF),
                                     limit=limit, with_payload=True).points


def search(store: Store, embedder: Embedder, query: str, mode: str = "hybrid", limit: int | None = None) -> SearchRun:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    query = normalise_query(query)
    if not query:
        raise ValueError("empty query")
    limit = limit or config.retrieval()["search"]["limit"]
    run = SearchRun(str(uuid.uuid4()), query, mode, [])
    for rank, point in enumerate(_points(store, embedder, query, mode, limit), start=1):
        p = point.payload
        run.chunk_text[p["chunk_id"]] = p["text"]
        run.results.append(Citation(
            citation_id=f"CIT-{rank:06d}",
            document_id=p["document_id"],
            document_version=p["document_version"],
            title=p["title"],
            page=p["page_start"],
            section=f"{p['section_id']} {p['section_title']}".strip(),
            chunk_id=p["chunk_id"],
            source_url=p["source_url"],
            quote_or_excerpt=excerpt(p["text"], p["permission_basis"]),
            rank=rank,
            score=float(point.score),
            section_id=p["section_id"],
            page_end=p["page_end"],
            permission_basis=p["permission_basis"],
            approval_status=p["approval_status"],
        ))
    return run
