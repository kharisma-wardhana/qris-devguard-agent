"""Citation validator (p2 spec Behaviour 4, AC #8; TRD §12.4).

Checks one cited item against the search run it claims to come from:
  not_retrieved_in_run - its citation_id/chunk_id pair isn't in this run's results
  source_not_approved  - the document/version isn't approved in the current manifest
  section_unresolved   - section missing or not the retrieved one, or page outside the chunk's pages
  excerpt_too_long     - longer than the licence limit for its permission basis
  excerpt_not_in_chunk - the excerpt is not a literal part of the retrieved chunk text
Whether the excerpt *supports* a statement is left to human labelling (§12.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.rag import config
from app.rag.search import SearchRun

VALID = "valid"
NOT_RETRIEVED, NOT_APPROVED = "not_retrieved_in_run", "source_not_approved"
SECTION_UNRESOLVED, TOO_LONG, NOT_IN_CHUNK = "section_unresolved", "excerpt_too_long", "excerpt_not_in_chunk"


@dataclass(frozen=True)
class Verdict:
    citation_id: str
    result: str                       # valid | one of the reasons above


def approved_versions(manifest_entries: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {(e["document_id"], str(e.get("document_version")))
            for e in manifest_entries if e.get("approval_status") == "approved"}


def validate(run: SearchRun, cited: dict[str, Any], approved: set[tuple[str, str]]) -> Verdict:
    cid = str(cited.get("citation_id", ""))
    retrieved = next((r for r in run.results if r.citation_id == cid), None)
    if retrieved is None or cited.get("chunk_id") != retrieved.chunk_id:
        return Verdict(cid, NOT_RETRIEVED)
    if (retrieved.document_id, retrieved.document_version) not in approved or retrieved.approval_status != "approved":
        return Verdict(cid, NOT_APPROVED)
    section_id = cited.get("section_id", retrieved.section_id)
    page = cited.get("page", retrieved.page)
    if not section_id or section_id != retrieved.section_id:
        return Verdict(cid, SECTION_UNRESOLVED)
    if page is not None and (retrieved.page is None or not retrieved.page <= page <= (retrieved.page_end or retrieved.page)):
        return Verdict(cid, SECTION_UNRESOLVED)
    excerpt = str(cited.get("quote_or_excerpt", ""))
    if len(excerpt) > config.excerpt_limit(retrieved.permission_basis):
        return Verdict(cid, TOO_LONG)
    if not excerpt or excerpt not in run.chunk_text.get(retrieved.chunk_id, ""):
        return Verdict(cid, NOT_IN_CHUNK)
    return Verdict(cid, VALID)


def validate_all(run: SearchRun, cited: list[dict[str, Any]], approved: set[tuple[str, str]]) -> list[Verdict]:
    return [validate(run, c, approved) for c in cited]
