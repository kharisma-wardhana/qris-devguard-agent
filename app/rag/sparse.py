"""Identifier-preserving BM25 sparse encoder (p2 spec AC #5).

Tokens are lowercased runs of letters/digits joined by `-`, `_`, `.` or `/`, so
`X-SIGNATURE`, `2007300`, `SHA256withRSA`, `/v1.0/access-token/b2b` and `HMAC_SHA512`
stay single terms. No stemming (the corpus is Indonesian + English).

Document weights are BM25 term-frequency saturation:
    w(t) = tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avg_doc_len))
Query weights are 1 per distinct term. Qdrant multiplies by IDF (`modifier: idf`).
Terms are hashed to 32-bit indices with BLAKE2b, so encoding is stable across runs.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass

from app.rag import config

TOKEN = re.compile(r"[a-z0-9]+(?:[-_./][a-z0-9]+)*")


@dataclass(frozen=True)
class Sparse:
    indices: list[int]
    values: list[float]


def tokenize(text: str) -> list[str]:
    return TOKEN.findall(text.lower())


def term_index(term: str) -> int:
    return int.from_bytes(hashlib.blake2b(term.encode("utf-8"), digest_size=4).digest(), "big")


def _to_sparse(weights: dict[int, float]) -> Sparse:
    items = sorted(weights.items())
    return Sparse([i for i, _ in items], [w for _, w in items])


def encode_document(text: str, k1: float | None = None, b: float | None = None,
                    avg_doc_len: float | None = None) -> Sparse:
    cfg = config.retrieval()["sparse"]
    k1 = cfg["k1"] if k1 is None else k1
    b = cfg["b"] if b is None else b
    avgdl = cfg["avg_doc_len"] if avg_doc_len is None else avg_doc_len
    terms = tokenize(text)
    dl = len(terms)
    weights: dict[int, float] = {}
    for term, tf in Counter(terms).items():
        w = tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avgdl))
        idx = term_index(term)
        weights[idx] = weights.get(idx, 0.0) + w          # a hash collision adds, never drops
    return _to_sparse(weights)


def encode_query(text: str) -> Sparse:
    return _to_sparse({term_index(t): 1.0 for t in set(tokenize(text))})
