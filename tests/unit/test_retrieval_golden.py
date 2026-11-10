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
APPROVED = manifest.retrievable_document_ids(           # approved documents; the p4 fixture set is not one
    manifest.load_manifest(ROOT / "knowledge_base" / "manifests" / "sources.yaml"))


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


# --- p3: subsets (spec AC #5) ------------------------------------------------

P2_ORIGINAL_HASH = "8956f2bbf8621e2219530de0c9011ac708d6d1aad4ca86c245fafbac8ab9ef65"   # at commit 674b0f7


def test_original_thirty_unchanged_since_p2():
    """Regression: P3 may only append. The original 30 entries' content is pinned by hash."""
    _, queries = metrics.load_golden(GOLDEN)
    assert sum(q.subset == "original" for q in queries) == 30
    assert metrics.original_content_hash(queries) == P2_ORIGINAL_HASH


def test_set_defaults_to_original():
    _, queries = metrics.load_golden(SAMPLE)
    assert {q.subset for q in queries if q.id == "s01"} == {"original"}


def gq(i, lang, doc, subset="hard"):
    return metrics.GoldQuery(f"h{i:02d}", lang, "q", frozenset({(doc, "1")}), "me", "2026-11-09", subset)


def test_hard_rules_count_language_and_coverage():
    docs = sorted(APPROVED)
    few = [gq(i, "en", docs[0]) for i in range(5)]
    errors = metrics.schema_errors(few, APPROVED, complete=False, require_hard=True)
    assert "hard set: expected at least 20 queries, found 5" in errors
    assert "hard set: only 0 'id' queries (need 8)" in errors
    assert any(e.startswith("hard set: documents with no query:") for e in errors)
    good = [gq(i, "id" if i % 2 else "en", docs[i % len(docs)]) for i in range(20)]
    assert metrics.schema_errors(good, APPROVED, complete=False, require_hard=True) == []


def test_unknown_subset_rejected():
    errors = metrics.schema_errors([gq(1, "en", sorted(APPROVED)[0], subset="easy")], APPROVED, complete=False)
    assert errors == ["h01: set must be one of ['hard', 'original']"]


def _has_hard() -> bool:
    return GOLDEN.exists() and any(q.subset == "hard" for q in metrics.load_golden(GOLDEN)[1])


@pytest.mark.skipif(not _has_hard(), reason="hard queries are merged in p3 plan step 7")
def test_real_golden_set_hard_subset_complete_and_reviewed():
    _, queries = metrics.load_golden(GOLDEN)
    assert metrics.schema_errors(queries, APPROVED, complete=True, require_hard=True) == []
