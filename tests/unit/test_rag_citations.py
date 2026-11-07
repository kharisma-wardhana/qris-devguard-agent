"""Citation validator (p2 spec AC #8) — five rejection reasons and one pass."""

import pytest

from app.rag import citations as cit
from app.rag.search import Citation, SearchRun

CHUNK_TEXT = "1.3 Timestamp format\n\nX-TIMESTAMP uses ISO-8601 with a +07:00 offset, for example 2026-11-07T10:00:00+07:00."


def citation(**over):
    base = dict(citation_id="CIT-000001", document_id="synthetic-qris-mpm-service-spec", document_version="0.1.0",
                title="QRIS MPM (SYNTHETIC — not an official ASPI/BI document)", page=None,
                section="1.3 1.3 Timestamp format", chunk_id="chunk-a", source_url=None,
                quote_or_excerpt="X-TIMESTAMP uses ISO-8601", rank=1, score=0.9, section_id="1.3", page_end=None,
                permission_basis="team_authored", approval_status="approved")
    base.update(over)
    return Citation(**base)


@pytest.fixture
def run():
    r = SearchRun("run-1", "timestamp", "hybrid", [citation()])
    r.chunk_text["chunk-a"] = CHUNK_TEXT
    return r


APPROVED = {("synthetic-qris-mpm-service-spec", "0.1.0")}


def cited(**over):
    base = {"citation_id": "CIT-000001", "chunk_id": "chunk-a", "section_id": "1.3",
            "quote_or_excerpt": "X-TIMESTAMP uses ISO-8601"}
    base.update(over)
    return base


def test_valid_citation_passes(run):
    assert cit.validate(run, cited(), APPROVED).result == cit.VALID


@pytest.mark.parametrize("over", [{"citation_id": "CIT-000099"}, {"chunk_id": "chunk-other"}])
def test_not_retrieved_in_run(run, over):
    assert cit.validate(run, cited(**over), APPROVED).result == cit.NOT_RETRIEVED


def test_source_not_approved(run):
    assert cit.validate(run, cited(), {("synthetic-qris-mpm-service-spec", "0.2.0")}).result == cit.NOT_APPROVED


def test_source_not_approved_when_payload_status_changed():
    r = SearchRun("run-2", "q", "hybrid", [citation(approval_status="superseded")])
    r.chunk_text["chunk-a"] = CHUNK_TEXT
    assert cit.validate(r, cited(), APPROVED).result == cit.NOT_APPROVED


@pytest.mark.parametrize("over", [{"section_id": "4.2"}, {"section_id": ""}, {"page": 3}])
def test_section_unresolved(run, over):
    assert cit.validate(run, cited(**over), APPROVED).result == cit.SECTION_UNRESOLVED


def test_page_inside_chunk_range_resolves():
    r = SearchRun("run-3", "q", "hybrid", [citation(page=3, page_end=5, permission_basis="reference_only")])
    r.chunk_text["chunk-a"] = CHUNK_TEXT
    assert cit.validate(r, cited(page=4), APPROVED).result == cit.VALID
    assert cit.validate(r, cited(page=6), APPROVED).result == cit.SECTION_UNRESOLVED


def test_excerpt_too_long_for_reference_only():
    long_text = "x " * 400
    r = SearchRun("run-4", "q", "hybrid", [citation(permission_basis="reference_only")])
    r.chunk_text["chunk-a"] = long_text
    assert cit.validate(r, cited(quote_or_excerpt=long_text[:301]), APPROVED).result == cit.TOO_LONG
    assert cit.validate(r, cited(quote_or_excerpt=long_text[:300]), APPROVED).result == cit.VALID


@pytest.mark.parametrize("excerpt", ["X-TIMESTAMP uses UNIX epoch", ""])
def test_excerpt_not_in_chunk(run, excerpt):
    assert cit.validate(run, cited(quote_or_excerpt=excerpt), APPROVED).result == cit.NOT_IN_CHUNK


def test_approved_versions_from_manifest():
    entries = [{"document_id": "a", "document_version": "1.0", "approval_status": "approved"},
               {"document_id": "b", "document_version": "1.0", "approval_status": "candidate"}]
    assert cit.approved_versions(entries) == {("a", "1.0")}
