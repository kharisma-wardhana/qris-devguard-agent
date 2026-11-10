"""policy_check rules 1-8 and answer assembly (p5 spec AC #2, #8)."""

from __future__ import annotations

import pytest

from app.agent import answer, policy

EVIDENCE = [
    {"citation_id": "CIT-000001", "document_id": "synthetic-qris-mpm-service-spec", "document_version": "0.1.0",
     "title": "QRIS MPM (SYNTHETIC)", "section": "1.4", "page": None, "source_url": None,
     "quote_or_excerpt": "stringToSign = ...", "permission_basis": "team_authored"},
    {"citation_id": "CIT-000002", "document_id": "synthetic-qr-validity", "document_version": "0.1.0",
     "title": "QR Validity", "section": "1", "quote_or_excerpt": "15 minutes", "permission_basis": "team_authored"},
    {"citation_id": "CIT-000003", "document_id": "synthetic-qr-validity", "document_version": "0.2.0",
     "title": "QR Validity", "section": "1", "quote_or_excerpt": "30 minutes", "permission_basis": "team_authored"},
]
SIG_INVALID = {"tool": "validate_test_signature", "ok": True,
               "data": {"status": "INVALID", "diagnostic_code": "SIG_WRONG_SEPARATOR", "profile": "p",
                        "test_payload_ref": "sig-at-wrong-separator", "assumptions": []}}
SIG_VALID = {"tool": "validate_test_signature", "ok": True, "data": {**SIG_INVALID["data"], "status": "VALID",
                                                                     "diagnostic_code": "SIG_VALID"}}
SIG_INCONCLUSIVE = {"tool": "validate_test_signature", "ok": True,
                    "data": {**SIG_INVALID["data"], "status": "INCONCLUSIVE", "diagnostic_code": "SIG_KEY_UNAVAILABLE"}}
CODE_NF = {"tool": "lookup_response_code", "ok": True,
           "data": {"status": "NOT_FOUND", "code": "4044700", "api_family": "qris-mpm", "spec_version": "0.1.0",
                    "match": None, "suggestions": []}}
FORBIDDEN = {"tool": "validate_test_signature", "ok": False, "data": {"code": "FORBIDDEN"}}


def f(text, status="confirmed", cites=(), refs=()):
    return {"text": text, "status": status, "citation_ids": list(cites), "validator_refs": list(refs)}


def composed(*findings, summary="s", steps=()):
    return {"summary": summary, "findings": list(findings), "next_steps": list(steps), "limitations": []}


def run(c, results=(), flags=None, code=None):
    return policy.check(c, evidence=EVIDENCE, results=list(results), flags=flags or {}, code=code)


def rules(actions):
    return {a["rule"] for a in actions}


def test_all_sections_present_and_sources_from_metadata():
    final, _ = run(composed(f("separator wrong", cites=["CIT-000001"], refs=[0])), [SIG_INVALID])
    assert tuple(final) == answer.SECTIONS
    assert final["sources"][0]["title"] == "QRIS MPM (SYNTHETIC)" and final["sources"][0]["synthetic"] is True
    assert final["validation_results"][0]["result"] == "INVALID" and final["confidence"] == "high"


def test_rule1_unretrieved_citation_dropped_and_finding_downgraded():
    final, actions = run(composed(f("made up", cites=["CIT-999999"]), f("ok", cites=["CIT-000001"])))
    assert final["findings"][0]["citation_ids"] == [] and final["findings"][0]["status"] == "unverified"
    assert all(s["citation_id"] != "CIT-999999" for s in final["sources"]) and 1 in rules(actions)


def test_rule1_unknown_validator_ref_dropped():
    final, actions = run(composed(f("x", refs=[5])), [SIG_INVALID])
    assert final["findings"][0]["validator_refs"] == [] and 1 in rules(actions)


def test_rule2_confirmed_on_non_decisive_result_downgraded():
    final, actions = run(composed(f("forbidden, so...", refs=[0])), [FORBIDDEN])
    assert final["findings"][0]["status"] == "unverified" and 2 in rules(actions)


@pytest.mark.parametrize("claim", [
    "Your signature is valid.", "The valid signature matches.", "signature verified against the key",
    "Tanda tangan Anda sudah valid.", "signature ini sah",
])
def test_rule3_valid_signature_claim_removed_without_valid_result(claim):
    final, actions = run(composed(f(claim, cites=["CIT-000001"]), f("separator", refs=[0]), summary=claim), [SIG_INVALID])
    assert all(claim not in x["text"] for x in final["findings"]) and claim not in final["summary"]
    assert 3 in rules(actions)


@pytest.mark.parametrize("text", ["The signature is invalid.", "The signature is not valid.",
                                  "Tanda tangan tidak valid.", "signature bukan valid"])
def test_rule3_negations_are_kept(text):
    final, actions = run(composed(f(text, refs=[0])), [SIG_INVALID])
    assert final["findings"][0]["text"] == text and 3 not in rules(actions)


def test_rule3_valid_claim_allowed_with_valid_result():
    final, actions = run(composed(f("The signature is valid.", refs=[0])), [SIG_VALID])
    assert final["findings"][0]["status"] == "confirmed" and 3 not in rules(actions)


def test_rule4_not_found_never_gets_a_meaning():
    final, actions = run(composed(f("4044700 means the merchant is blocked", "confirmed", refs=[0]),
                                  summary="4044700 means merchant blocked"), [CODE_NF], code="4044700")
    assert final["summary"].startswith("Response code 4044700 was not found") and "blocked" not in final["summary"]
    assert final["findings"][0]["status"] == "unverified" and 4 in rules(actions)


def test_rule5_inconclusive_is_not_a_verdict():
    final, actions = run(composed(f("the signature is wrong", refs=[0], cites=["CIT-000001"])), [SIG_INCONCLUSIVE])
    assert final["findings"][0]["status"] == "unverified" and 5 in rules(actions)
    assert any("inconclusive" in lim for lim in final["limitations"])


def test_rule6_conflict_keeps_both_versions():
    final, actions = run(composed(f("15 minutes in 0.1.0", "likely", cites=["CIT-000002"])), flags={"version_conflict": True})
    assert {s["document_version"] for s in final["sources"] if s["document_id"] == "synthetic-qr-validity"} == {"0.1.0", "0.2.0"}
    assert 6 in rules(actions) and any("more than one version" in lim for lim in final["limitations"])


def test_rule7_secret_in_model_output_redacted_but_excerpts_untouched():
    token = "eyJhbGciOiJIUzI1NiJ9SYNTHETICTOKENVALUE0123"
    final, actions = run(composed(f(f"use Authorization: Bearer {token}", cites=["CIT-000001"]),
                                  summary=f"client_secret=abc{token}"))
    assert token not in str(final) and 7 in rules(actions)
    assert final["sources"][0]["quote_or_excerpt"] == "stringToSign = ..."


def test_rule8_abstains_when_nothing_supported_is_left():
    final, actions = run(composed(f("Your signature is valid.")), [SIG_INVALID])
    assert final["findings"] == [] and final["summary"].startswith("No supported diagnosis") and 8 in rules(actions)
    assert final["validation_results"][0]["result"] == "INVALID"     # verdicts still shown


def test_fallback_has_verdicts_only():
    fb = answer.fallback([SIG_INVALID, FORBIDDEN])
    assert len(fb["findings"]) == 1 and "INVALID" in fb["findings"][0]["text"] and fb["findings"][0]["validator_refs"] == [0]
    final, _ = run(fb, [SIG_INVALID, FORBIDDEN], flags={"fallback": True})
    assert final["confidence"] == "low" and final["findings"][0]["status"] == "confirmed"


@pytest.mark.parametrize("builder", [answer.refusal, lambda: answer.draft_only("ticket"),
                                     lambda: answer.clarification(["endpoint_id"]), lambda: answer.abstention("x")])
def test_fixed_answers_have_every_section(builder):
    assert set(answer.SECTIONS) <= set(builder())
