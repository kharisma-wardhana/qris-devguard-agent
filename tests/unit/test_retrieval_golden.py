"""The labelled retrieval set (p2 spec AC #11).

The schema check runs on a 2-query sample always. The full 30-query check runs on
evals/datasets/retrieval_golden.yaml once it exists (plan step 11).
"""

from pathlib import Path

import pytest

from app.rag import manifest, metrics

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "tests" / "fixtures" / "rag" / "golden_sample.yaml"
GOLDEN = ROOT / "evals" / "datasets" / "retrieval_golden.yaml"
APPROVED = {e["document_id"] for e in manifest.load_manifest(ROOT / "knowledge_base" / "manifests" / "sources.yaml")
            if e.get("approval_status") == "approved"}


def test_sample_passes_schema():
    _, queries = metrics.load_golden(SAMPLE)
    assert len(queries) == 2
    assert metrics.schema_errors(queries, APPROVED, complete=False) == []


def test_sample_is_not_a_complete_set():
    _, queries = metrics.load_golden(SAMPLE)
    errors = metrics.schema_errors(queries, APPROVED, complete=True)
    assert "expected 30 queries, found 2" in errors
    assert any("documents with no query" in e for e in errors)


def test_schema_catches_bad_entries():
    bad = [metrics.GoldQuery("x1", "fr", "q", frozenset({("not-approved", "1")}), None, None),
           metrics.GoldQuery("x1", "en", " ", frozenset(), "me", "2026-11-07")]
    errors = metrics.schema_errors(bad, APPROVED, complete=True)
    for expected in ("duplicate query ids", "x1: language must be one of", "x1: not-approved is not an approved document",
                     "x1: empty query", "x1: no gold sections", "x1: not reviewed"):
        assert any(e.startswith(expected) for e in errors), expected


@pytest.mark.skipif(not GOLDEN.exists(), reason="retrieval_golden.yaml is written in p2 plan step 11")
def test_real_golden_set_is_complete_and_reviewed():
    _, queries = metrics.load_golden(GOLDEN)
    assert metrics.schema_errors(queries, APPROVED, complete=True) == []
