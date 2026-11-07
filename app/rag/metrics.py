"""Retrieval metrics and the labelled query set (p2 spec Behaviour 5; AC #11, #13).

Gold labels are (document_id, section_id) pairs, so they survive re-chunking.
A query is a hit at k if any of its top-k results belongs to a gold section.
  Recall@k = share of queries with a hit at k
  MRR@k    = mean of 1/rank of the first gold hit within k (0 if none)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

LANGUAGES = {"id", "en"}
REQUIRED_QUERIES = 30
MIN_PER_LANGUAGE = 10

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


@dataclass(frozen=True)
class GoldQuery:
    id: str
    language: str
    query: str
    gold: frozenset[Section]
    reviewed_by: str | None
    reviewed_at: str | None


def load_golden(path: Path) -> tuple[dict[str, Any], list[GoldQuery]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    queries = [GoldQuery(q["id"], q["language"], q["query"],
                         frozenset((g["document_id"], str(g["section_id"])) for g in q["gold"]),
                         q.get("reviewed_by"), None if q.get("reviewed_at") is None else str(q["reviewed_at"]))
               for q in data.get("queries") or []]
    return data, queries


def schema_errors(queries: list[GoldQuery], approved_ids: set[str], *, complete: bool) -> list[str]:
    """Problems with the set. `complete=True` also enforces the 30-query AC #11 shape and reviews."""
    errors = []
    ids = [q.id for q in queries]
    if len(ids) != len(set(ids)):
        errors.append("duplicate query ids")
    for q in queries:
        if q.language not in LANGUAGES:
            errors.append(f"{q.id}: language must be one of {sorted(LANGUAGES)}")
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
        if len(queries) != REQUIRED_QUERIES:
            errors.append(f"expected {REQUIRED_QUERIES} queries, found {len(queries)}")
        for lang in sorted(LANGUAGES):
            n = sum(q.language == lang for q in queries)
            if n < MIN_PER_LANGUAGE:
                errors.append(f"only {n} '{lang}' queries (need {MIN_PER_LANGUAGE})")
        covered = {doc for q in queries for doc, _ in q.gold}
        missing = sorted(approved_ids - covered)
        if missing:
            errors.append(f"documents with no query: {', '.join(missing)}")
    return errors
