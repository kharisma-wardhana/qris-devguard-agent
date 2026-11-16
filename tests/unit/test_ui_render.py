"""p9 plain-text rendering (docs/p9-demo-ui-alerts/spec.md Behaviour §3, §4, §6; AC #15)."""

from __future__ import annotations

import pytest

from app.ui import render

HOSTILE = "[click here](https://evil.example/steal) <img src=x onerror=alert(1)> **bold**"

ANSWER = {
    "summary": f"Signature is invalid. {HOSTILE}",
    "findings": [{"text": "maybe the separator", "status": "likely", "citation_ids": ["C2"]},
                 {"text": "validator says invalid", "status": "confirmed", "citation_ids": [], "validator_refs": [0]},
                 {"text": "unclear clock skew", "status": "unverified", "citation_ids": []}],
    "validation_results": [{"tool": "validate_test_signature", "result": "INVALID",
                            "reference": {"test_case": "sig-at-wrong-separator", "fixture_id": "syn-rsa-01", "profile": None},
                            "details": ["STRING_TO_SIGN_MISMATCH"], "limitations": ["Synthetic test material only."]}],
    "next_steps": [{"text": "Use | as the separator", "citation_ids": ["C1"]}],
    "sources": [{"citation_id": "C1", "title": "SNAP signature", "document_id": "snap-std", "document_version": "1.0.2",
                 "section": "2.1", "page": 7, "source_url": "https://example.org/snap.pdf",
                 "quote_or_excerpt": f"stringToSign = ... {HOSTILE}", "synthetic": False}],
    "confidence": "high",
    "limitations": ["Synthetic test material only."],
}


def flat(sections):
    return "\n".join(f"{h}\n" + "\n".join(lines) for h, lines in sections)


def test_sections_follow_the_response_contract_order():
    heads = [h for h, _ in render.answer_sections(ANSWER)]
    assert heads == ["Summary", "Findings", "Validation results", "Next steps", "Sources", "Confidence and limitations"]


def test_findings_grouped_confirmed_likely_unverified():
    findings = dict(render.answer_sections(ANSWER))["Findings"]
    assert [ln.split("]")[0] for ln in findings] == ["[confirmed", "[likely", "[unverified"]
    assert findings[1].endswith("[C2]")


def test_validation_sources_and_confidence_lines():
    text = flat(render.answer_sections(ANSWER))
    assert "validate_test_signature -> INVALID (test_case=sig-at-wrong-separator, fixture_id=syn-rsa-01)" in text
    assert "detail: STRING_TO_SIGN_MISMATCH" in text and "Confidence: high" in text
    assert "[C1] SNAP signature - snap-std v1.0.2, section 2.1, page 7" in text


def test_hostile_text_passes_through_as_literal_text():
    """Rendering never interprets text: the planted link and HTML come out character for character, to be shown by a
    text element (the page test checks no markdown element carries them)."""
    text = flat(render.answer_sections(ANSWER))
    assert text.count(HOSTILE) == 2


def test_empty_and_missing_answer():
    sections = dict(render.answer_sections(None))
    assert sections["Summary"] == ["(no summary)"] and sections["Findings"] == ["(none)"]
    assert sections["Sources"] == ["(no sources cited)"]


def test_action_and_draft_sections():
    created = dict(render.answer_sections({**ANSWER, "action": {"type": "create_support_ticket", "status": "created",
                                                                  "target": "local sandbox_tickets table",
                                                                  "ticket_id": "t-1"}}))
    assert "ticket: t-1" in created["Action"]
    rejected = dict(render.answer_sections({**ANSWER, "action": {"type": "create_support_ticket", "status": "rejected",
                                                                   "target": "x", "ticket_id": None}}))
    assert "nothing was created" in rejected["Action"][1]
    draft = dict(render.answer_sections({**ANSWER, "draft": {"action": "create_support_ticket", "status": "draft_only",
                                                               "summary": "s"}}))
    assert "Draft (not executed)" in draft


def test_approval_lines_and_outcomes():
    view = {"approval_id": "a-1", "status": "pending", "requester_id": "demo-dev", "expires_at": "2026-11-12T05:00:00",
            "action": {"type": "create_support_ticket", "target": "local sandbox_tickets table",
                       "payload": {"title": "Escalation: x", "body": "Question: y", "priority": "normal"}},
            "expected_side_effect": "one row", "evidence_ids": ["C1", "VAL-0"], "ticket_id": None}
    text = "\n".join(render.approval_lines(view))
    assert "approval: a-1" in text and "evidence: C1, VAL-0" in text and "title: Escalation: x" in text
    assert render.decision_outcome({"approval": {"status": "executed", "ticket_id": "t-9"}}).endswith("t-9 was created.")
    assert render.decision_outcome({"approval": {"status": "rejected", "ticket_id": None}}) == "Rejected: nothing was created."


def test_alert_lines():
    header, lines = render.alert_lines({"severity": "ALERT", "title": "p95 latency", "body": "6100 ms", "window": "5m",
                                        "received_at": "2026-11-12T03:00:00+00:00", "id": "evt-1", "monitor_id": "m"})
    assert header == "ALERT (firing) - p95 latency" and lines[0] == "6100 ms" and "alert id: evt-1" in lines[2]
    assert render.alert_lines({"severity": "OK", "title": "t"})[0].startswith("OK (recovered)")


def test_short_id():
    assert render.short_id("abcdefghijklmnop") == "abcdefghij..." and render.short_id("abc") == "abc"


# --- p9 ui-refresh (docs/p9-ui-refresh/spec.md Behaviour §2, §3, §6, §7; AC #8, #9) --------------------------------

def test_overview_counts_from_real_data_and_none_for_no_figure():
    threads = [{"status": "awaiting_approval"}, {"status": "completed"}, {"status": "awaiting_approval"}]
    assert render.overview_counts(threads, [{"approval_id": "a"}], {"mcp": "up", "llm": "down"}) == \
        {"threads": 3, "awaiting": 2, "pending": 1, "checks_up": 1, "checks_total": 2}
    assert render.overview_counts([], [], {"mcp": "up", "llm": "up"}) == \
        {"threads": 0, "awaiting": 0, "pending": 0, "checks_up": 2, "checks_total": 2}
    assert render.overview_counts(None, None, None) == \
        {"threads": None, "awaiting": None, "pending": None, "checks_up": None, "checks_total": None}
    assert render.overview_counts(None, None, {})["checks_total"] is None


@pytest.mark.parametrize("value, text", [(0.4356, "0.4356"), (0.92, "0.92"), (1.0, "1"), (0, "0"), (True, "true"),
                                         (False, "false"), (None, "(no value)"), ({"passed": 3, "of": 4}, "passed=3, of=4"),
                                         ({}, "(no value)")])
def test_gate_value(value, text):
    assert render.gate_value(value) == text


@pytest.mark.parametrize("threshold, text", [({"min": 0.9}, ">= 0.9"), ({"max": 0}, "<= 0"),
                                             ({"must_pass": True}, "must pass"), ({"min": 4, "of": 4}, ">= 4 of 4"),
                                             ({"other": 1}, "other=1"), (None, "(no threshold)"), ({}, "(no threshold)")])
def test_gate_threshold(threshold, text):
    assert render.gate_threshold(threshold) == text


def test_gate_text_keeps_hostile_strings_literal():
    assert HOSTILE in render.gate_value(HOSTILE)
    assert HOSTILE in render.gate_value({HOSTILE: 1})
    assert HOSTILE in render.gate_threshold({HOSTILE: 1})


def test_source_lines():
    full = {"document_id": "snap-std", "title": HOSTILE, "publisher": "ASPI / Bank Indonesia", "kind": "specification",
            "document_version": "1.0.2", "effective_date": "2024-09", "synthetic": False}
    assert render.source_lines(full) == [HOSTILE, "snap-std · ASPI / Bank Indonesia",
                                         "version 1.0.2, effective 2024-09, specification"]
    assert render.source_lines({"document_id": "x"}) == ["(untitled)", "x", "(no version or date)"]
