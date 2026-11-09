"""Retrieval metrics and the labelled query set (p2 spec Behaviour 5, AC #11, #13; p3 spec AC #4, #5).

Gold labels are (document_id, section_id) pairs, so they survive re-chunking.
A query is a hit at k if any of its top-k results belongs to a gold section.
  Recall@k = share of queries with a hit at k
  MRR@k    = mean of 1/rank of the first gold hit within k (0 if none)
  nDCG@k   = binary relevance; a result counts only if it is the FIRST result from one of
             the query's gold sections (later chunks of the same section count 0, so long
             sections can't fill the top k). Ideal DCG = min(|gold sections|, k) hits at the top.

The set has two subsets (p3): `original` (the reviewed P2 queries, default when `set` is
absent) and `hard` (symptom-style queries added in P3).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

LANGUAGES = {"id", "en"}
SUBSETS = {"original", "hard"}
REQUIRED_QUERIES = 30             # original subset, exactly (p2 AC #11)
MIN_PER_LANGUAGE = 10             # original subset
MIN_HARD_QUERIES = 20             # hard subset, at least (p3 AC #5)
MIN_HARD_PER_LANGUAGE = 8

Section = tuple[str, str]


def first_hit_rank(ranked: list[Section], gold: set[Section], k: int) -> int | None:
    for rank, section in enumerate(ranked[:k], start=1):
        if section in gold:
            return rank
    return None


def recall_at_k(runs: list[tuple[list[Section], set[Section]]], k: int) -> float:
    if not runs:
        return 0.0
    return sum(first_hit_rank(r, g, k) is not None for r, g in runs) / len(runs)


def mrr_at_k(runs: list[tuple[list[Section], set[Section]]], k: int) -> float:
    if not runs:
        return 0.0
    return sum(1 / rank for r, g in runs if (rank := first_hit_rank(r, g, k))) / len(runs)


def ndcg_one(ranked: list[Section], gold: set[Section], k: int) -> float:
    seen: set[Section] = set()
    dcg = 0.0
    for rank, section in enumerate(ranked[:k], start=1):
        if section in gold and section not in seen:
            dcg += 1 / math.log2(rank + 1)
            seen.add(section)
    ideal = sum(1 / math.log2(i + 1) for i in range(1, min(len(gold), k) + 1))
    return dcg / ideal if ideal else 0.0


def ndcg_at_k(runs: list[tuple[list[Section], set[Section]]], k: int) -> float:
    if not runs:
        return 0.0
    return sum(ndcg_one(r, g, k) for r, g in runs) / len(runs)


@dataclass(frozen=True)
class GoldQuery:
    id: str
    language: str
    query: str
    gold: frozenset[Section]
    reviewed_by: str | None
    reviewed_at: str | None
    subset: str = "original"


def load_golden(path: Path) -> tuple[dict[str, Any], list[GoldQuery]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("golden set must be a YAML mapping with a `queries` list")
    queries = [GoldQuery(q["id"], q["language"], q["query"],
                         frozenset((g["document_id"], str(g["section_id"])) for g in q["gold"]),
                         q.get("reviewed_by"), None if q.get("reviewed_at") is None else str(q["reviewed_at"]),
                         q.get("set", "original"))
               for q in data.get("queries") or []]
    return data, queries


def original_content_hash(queries: list[GoldQuery]) -> str:
    """Hash of the original subset's content, to prove later edits left it unchanged (p3 AC #5)."""
    rows = [{"id": q.id, "language": q.language, "query": q.query, "gold": sorted(q.gold),
             "reviewed_by": q.reviewed_by, "reviewed_at": q.reviewed_at}
            for q in sorted(queries, key=lambda q: q.id) if q.subset == "original"]
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def schema_errors(queries: list[GoldQuery], approved_ids: set[str], *, complete: bool,
                  require_hard: bool = False) -> list[str]:
    """Problems with the set.
    `complete=True` enforces the original subset's shape (exactly 30, >= 10 per language, all
    documents covered) and reviews. `require_hard=True` also enforces the hard subset (p3)."""
    errors = []
    ids = [q.id for q in queries]
    if len(ids) != len(set(ids)):
        errors.append("duplicate query ids")
    for q in queries:
        if q.language not in LANGUAGES:
            errors.append(f"{q.id}: language must be one of {sorted(LANGUAGES)}")
        if q.subset not in SUBSETS:
            errors.append(f"{q.id}: set must be one of {sorted(SUBSETS)}")
        if not q.query.strip():
            errors.append(f"{q.id}: empty query")
        if not q.gold:
            errors.append(f"{q.id}: no gold sections")
        for doc, section in q.gold:
            if doc not in approved_ids:
                errors.append(f"{q.id}: {doc} is not an approved document")
            if not section:
                errors.append(f"{q.id}: empty section_id for {doc}")
        if complete and not (q.reviewed_by and q.reviewed_at):
            errors.append(f"{q.id}: not reviewed")
    if complete:
        original = [q for q in queries if q.subset == "original"]
        if len(original) != REQUIRED_QUERIES:
            errors.append(f"expected {REQUIRED_QUERIES} queries, found {len(original)}")
        for lang in sorted(LANGUAGES):
            n = sum(q.language == lang for q in original)
            if n < MIN_PER_LANGUAGE:
                errors.append(f"only {n} '{lang}' queries (need {MIN_PER_LANGUAGE})")
        missing = sorted(approved_ids - {doc for q in original for doc, _ in q.gold})
        if missing:
            errors.append(f"documents with no query: {', '.join(missing)}")
    if require_hard:
        hard = [q for q in queries if q.subset == "hard"]
        if len(hard) < MIN_HARD_QUERIES:
            errors.append(f"hard set: expected at least {MIN_HARD_QUERIES} queries, found {len(hard)}")
        for lang in sorted(LANGUAGES):
            n = sum(q.language == lang for q in hard)
            if n < MIN_HARD_PER_LANGUAGE:
                errors.append(f"hard set: only {n} '{lang}' queries (need {MIN_HARD_PER_LANGUAGE})")
        missing = sorted(approved_ids - {doc for q in hard for doc, _ in q.gold})
        if missing:
            errors.append(f"hard set: documents with no query: {', '.join(missing)}")
    return errors
