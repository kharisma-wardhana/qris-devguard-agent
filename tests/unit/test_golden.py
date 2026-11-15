"""Golden dataset schema and the deterministic answer checks (p9-final spec AC #7, #8)."""

import copy
from pathlib import Path

import pytest

from app.evaluation import ROOT, golden

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "evaluation"
SMALL = FIX / "golden_small.yaml"


def small():
    return golden.load_dataset(SMALL)


# ---------------------------------------------------------------- schema

def test_small_fixture_is_schema_valid():
    assert golden.schema_errors(small()) == []


def test_answer_item_needs_facts_and_sections():
    d = small()
    d["items"][0]["expected_facts"] = []
    d["items"][0]["supporting_sections"] = []
    errors = golden.schema_errors(d)
    assert len(errors) >= 2


@pytest.mark.parametrize("field,value", [("id", "q-1"), ("expected_behaviour", "maybe"), ("category", "other"),
                                         ("language", "fr"), ("reviewed_on", "15/11/2026")])
def test_schema_rejects_bad_values(field, value):
    d = small()
    d["items"][0][field] = value
    assert golden.schema_errors(d)


def test_schema_rejects_unknown_field_and_duplicate_id():
    d = small()
    d["items"][0]["extra"] = 1
    assert golden.schema_errors(d)
    d = small()
    d["items"][1]["id"] = d["items"][0]["id"]
    assert any("duplicate" in e for e in golden.schema_errors(d))


def test_unreviewed_lists_items_without_reviewer_and_date():
    assert golden.unreviewed(small()["items"]) == ["ga-003"]


def test_section_errors():
    items = small()["items"]
    sections = {"synthetic-qris-mpm-service-spec": {"1.1", "1.3", "2.4#1"}}
    assert golden.section_errors(items, sections) == []
    bad = copy.deepcopy(items)
    bad[0]["supporting_sections"][0]["section_id"] = "9.9"
    assert golden.section_errors(bad, sections) == ["ga-001: no section '9.9' in synthetic-qris-mpm-service-spec"]
    # a prefix matches numbered sub-items and subsections
    bad[0]["supporting_sections"][0]["section_id"] = "2.4"
    assert golden.section_errors(bad, sections) == []
    # documents whose PDFs aren't on this machine are skipped, not failed
    assert golden.section_errors(bad, {}) == []


# ---------------------------------------------------------------- the committed dataset

DATASET = ROOT / "evals" / "datasets" / "answer_golden.yaml"


@pytest.mark.skipif(not DATASET.exists(), reason="answer_golden.yaml not written yet")
def test_committed_dataset_is_valid_and_has_100_items():
    d = golden.load_dataset(DATASET)
    assert golden.schema_errors(d) == []
    assert len(d["items"]) == 100
    assert [i["id"] for i in d["items"]] == [f"ga-{n:03d}" for n in range(1, 101)]


@pytest.mark.skipif(not DATASET.exists(), reason="answer_golden.yaml not written yet")
def test_committed_dataset_sections_exist_in_the_corpus():
    d = golden.load_dataset(DATASET)
    assert golden.section_errors(d["items"], golden.corpus_sections()) == []


@pytest.mark.skipif(not DATASET.exists(), reason="answer_golden.yaml not written yet")
def test_committed_dataset_is_fully_reviewed():
    """Strict mode (plan step 13): skipped, with the count, until the developer has reviewed every item."""
    pending = golden.unreviewed(golden.load_dataset(DATASET)["items"])
    if pending:
        pytest.skip(f"{len(pending)} golden items not reviewed yet")
    assert pending == []


# ---------------------------------------------------------------- deterministic checks

def answer(**kw):
    base = {"summary": "Summary.", "findings": [], "validation_results": [], "next_steps": [], "sources": [],
            "limitations": [], "confidence": "medium"}
    base.update(kw)
    return base


def test_citation_check_counts_unretrieved_ids():
    a = answer(findings=[{"status": "confirmed", "text": "x", "citation_ids": ["c1", "c9"]}],
               sources=[{"citation_id": "c1"}])
    assert golden.citation_check(a, {"c1", "c2"}) == {"total": 2, "valid": 1, "invalid_ids": ["c9"]}
    assert golden.citation_check(answer(), set()) == {"total": 0, "valid": 0, "invalid_ids": []}


def test_signature_valid_claim_without_valid_verdict_is_fabricated():
    a = answer(summary="Your signature is valid.",
               validation_results=[{"tool": "validate_test_signature", "result": "INVALID"}])
    assert golden.fabricated_crypto(a) == ["signature_valid_claim_without_VALID"]
    a["validation_results"][0]["result"] = "VALID"
    assert golden.fabricated_crypto(a) == []
    assert golden.fabricated_crypto(answer(summary="The signature is not valid.")) == []


def test_payload_valid_claim_without_valid_verdict_is_fabricated():
    a = answer(findings=[{"status": "likely", "text": "The payload is valid against the schema.", "citation_ids": []}])
    assert golden.fabricated_crypto(a) == ["payload_valid_claim_without_valid"]
    a["validation_results"] = [{"tool": "validate_request_payload", "result": "valid"}]
    assert golden.fabricated_crypto(a) == []
    assert golden.fabricated_crypto(answer(summary="The payload is invalid at /headers.")) == []


def test_meaning_given_to_unknown_code_is_fabricated():
    vr = [{"tool": "lookup_response_code", "result": "NOT_FOUND", "reference": {"code": "4044700"}}]
    a = answer(validation_results=vr,
               findings=[{"status": "confirmed", "text": "4044700 means the merchant is blocked.", "citation_ids": []}])
    assert golden.fabricated_crypto(a) == ["meaning_given_to_unknown_code"]
    a["findings"][0]["status"] = "unverified"
    assert golden.fabricated_crypto(a) == []


def test_secret_findings_scan_answer_text_but_not_excerpts():
    planted = "Authorization: Bearer " + "abcDEF0123456789abcDEF0123456789"
    a = answer(next_steps=[{"text": f"Send {planted}", "citation_ids": []}])
    assert golden.secret_findings(a)
    clean = answer(sources=[{"citation_id": "c1", "quote_or_excerpt": planted}])
    assert golden.secret_findings(clean) == []


@pytest.mark.parametrize("out,expected", [
    ({"route": "refuse", "answer": answer()}, "refuse"),
    ({"route": "read", "answer": answer(validation_results=[{"tool": "lookup_response_code", "result": "NOT_FOUND"}])},
     "not_found"),
    ({"route": "clarify", "answer": answer()}, "abstain"),
    ({"route": "read", "policy_actions": [{"rule": 8}], "answer": answer()}, "abstain"),
    ({"route": "read", "answer": answer(findings=[{"status": "unverified", "text": "x"}])}, "abstain"),
    ({"route": "read", "answer": answer(findings=[{"status": "likely", "text": "x"}])}, "answer"),
])
def test_observed_behaviour(out, expected):
    assert golden.observed_behaviour(out) == expected


def test_build_record_caps_excerpts_and_runs_checks():
    item = small()["items"][0]
    long = "x" * 1000
    out = {"intent": "timestamp", "route": "read", "risk_level": "low", "trace": {"total_ms": 12},
           "answer": answer(findings=[{"status": "confirmed", "text": "ISO-8601", "citation_ids": ["c1"]}],
                            sources=[{"citation_id": "c1", "document_id": "d", "document_version": "1",
                                      "section": "1.3", "quote_or_excerpt": long}])}
    rec = golden.build_record(item, out, {"c1"})
    assert len(rec["citations"][0]["excerpt"]) <= 300
    assert rec["behaviour_match"] is True
    assert rec["checks"] == {"citations": {"total": 1, "valid": 1, "invalid_ids": []}, "fabricated_crypto": [],
                             "secret_findings": []}
    s = golden.summarise([rec])
    assert s["citation_validity"] == 1.0 and s["fabricated_crypto"] == 0 and s["secret_findings"] == 0


def test_tool_errors_are_recorded_and_counted():
    item = small()["items"][0]
    out = {"route": "read", "tool_calls": [{"tool": "search_qris_spec", "outcome": "RATE_LIMITED", "ms": 1}],
           "answer": answer(validation_results=[{"tool": "lookup_response_code", "result": "TOOL_UNAVAILABLE"}])}
    rec = golden.build_record(item, out, set())
    assert rec["tool_errors"] == ["RATE_LIMITED", "TOOL_UNAVAILABLE"]
    s = golden.summarise([rec])
    assert s["tool_errors"] == 1 and s["tool_error_ids"] == ["ga-001"]
