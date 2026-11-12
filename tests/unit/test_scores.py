"""Deterministic scores (p8 spec Behaviour §3; AC #18, #20): one fixture per score value."""

from __future__ import annotations

import copy

import pytest

from app.observability import scores

EVIDENCE = [{"citation_id": "CIT-000001"}, {"citation_id": "CIT-000002"}]
VALID = {"tool": "validate_test_signature", "ok": True, "data": {"status": "INVALID"}}
FINDING = {"text": "The separator is wrong.", "status": "confirmed", "citation_ids": ["CIT-000001"],
           "validator_refs": [0]}
STEP = {"text": "Compare with the cited section.", "citation_ids": ["CIT-000001"]}
ANSWER = {"summary": "s", "findings": [FINDING], "next_steps": [STEP], "limitations": []}


def state(**over):
    base = {"route": "read", "composed": copy.deepcopy(ANSWER), "final_answer": copy.deepcopy(ANSWER),
            "reranked_chunks": EVIDENCE, "validator_results": [VALID], "policy_actions": [],
            "tool_calls": [{"tool": "search_qris_spec", "outcome": "OK"},
                           {"tool": "validate_test_signature", "outcome": "OK"}]}
    return {**base, **over}


def as_dict(s):
    out = scores.compute(s)
    assert len({n for n, _, _ in out if n != "incident_event"}) == len([n for n, _, _ in out if n != "incident_event"])
    return {n: v for n, v, _ in out}


def test_a_clean_read_run_scores_one_everywhere():
    assert as_dict(state()) == {"citation_valid": 1, "answer_supported": 1, "policy_pass": 1, "tool_success": 1}


def test_an_invented_citation_gives_citation_valid_zero():
    composed = copy.deepcopy(ANSWER)
    composed["findings"][0]["citation_ids"] = ["CIT-999999"]
    assert as_dict(state(composed=composed))["citation_valid"] == 0


def test_a_citation_in_a_next_step_counts_too():
    composed = copy.deepcopy(ANSWER)
    composed["next_steps"][0]["citation_ids"] = ["CIT-000404"]
    assert as_dict(state(composed=composed))["citation_valid"] == 0


@pytest.mark.parametrize("change", [
    lambda a: a["findings"][0].update(citation_ids=[], validator_refs=[]),          # a finding with no reference
    lambda a: a["next_steps"][0].update(citation_ids=[]),                           # a next step with none
    lambda a: a["findings"][0].update(citation_ids=[], validator_refs=[1]),         # confirmed on a missing result
])
def test_unsupported_final_answer_gives_answer_supported_zero(change):
    final = copy.deepcopy(ANSWER)
    change(final)
    assert as_dict(state(final_answer=final))["answer_supported"] == 0


def test_confirmed_on_an_inconclusive_result_is_unsupported():
    final = copy.deepcopy(ANSWER)
    final["findings"][0].update(citation_ids=[])
    res = [{"tool": "validate_test_signature", "ok": True, "data": {"status": "INCONCLUSIVE"}}]
    assert as_dict(state(final_answer=final, validator_results=res))["answer_supported"] == 0


@pytest.mark.parametrize(("rule", "passes"), [(1, 0), (2, 0), (3, 0), (4, 0), (5, 0), (7, 0), (6, 1), (8, 1)])
def test_policy_pass_is_zero_only_for_a_correction(rule, passes):
    assert as_dict(state(policy_actions=[{"rule": rule, "action": "x", "target": "y"}]))["policy_pass"] == passes


def test_an_abstention_sends_no_answer_supported():
    got = as_dict(state(policy_actions=[{"rule": 8, "action": "abstained", "target": "answer"}]))
    assert "answer_supported" not in got and got["policy_pass"] == 1


def test_a_failed_tool_gives_tool_success_zero():
    calls = [{"tool": "search_qris_spec", "outcome": "SEARCH_UNAVAILABLE"}]
    assert as_dict(state(tool_calls=calls))["tool_success"] == 0


@pytest.mark.parametrize("route", ["refuse", "clarify", "draft"])
def test_refusal_clarification_and_draft_send_no_answer_scores(route):
    got = as_dict(state(route=route, tool_calls=[], policy_actions=[]))
    assert got == {"policy_pass": 1}


def test_answer_echo_is_sent_only_once_measured():
    assert "answer_echo" not in as_dict(state())
    assert as_dict(state(answer_echo=True))["answer_echo"] == 1
    assert as_dict(state(answer_echo=False))["answer_echo"] == 0


@pytest.mark.parametrize("code", ["APPROVAL_INVALID", "APPROVAL_MISMATCH", "AUDIT_UNAVAILABLE", "INTERNAL_ERROR"])
def test_each_single_event_code_gives_exactly_one_incident_with_the_code(code):
    calls = [{"tool": "search_qris_spec", "outcome": "OK"}, {"tool": "create_support_ticket", "outcome": code}]
    incidents = [x for x in scores.compute(state(tool_calls=calls)) if x[0] == "incident_event"]
    assert incidents == [("incident_event", 1, code)]


@pytest.mark.parametrize("code", ["OK", "FORBIDDEN", "SEARCH_UNAVAILABLE", "TOOL_UNAVAILABLE", "RATE_LIMITED"])
def test_other_outcomes_are_not_incidents(code):
    calls = [{"tool": "search_qris_spec", "outcome": code}]
    assert not [x for x in scores.compute(state(tool_calls=calls)) if x[0] == "incident_event"]


def test_a_resume_scores_only_its_own_calls():
    calls = [{"tool": "search_qris_spec", "outcome": "SEARCH_UNAVAILABLE"},
             {"tool": "create_support_ticket", "outcome": "OK"}]
    got = scores.compute(state(tool_calls=calls), since_call=1, answer_scores=False)
    assert got == [("tool_success", 1, None)]
