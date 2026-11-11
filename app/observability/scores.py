"""Deterministic scores for a run (p8 spec Behaviour §3; AC #18, #20). No LLM judges anything.

Computed from the final graph state, after finalize:

- `citation_valid`: 1 if every citation the model wrote (the composed answer, before policy_check) resolves to a
  chunk retrieved in this run. policy_check drops unresolved citations from the final answer, so measured there
  the score could never be 0: it measures the model, and the final answer is enforced either way. Read route only.
- `answer_supported`: on the final answer, every finding carries a citation or validator reference, every next
  step a citation, and every `confirmed` finding a citation or a decisive validator result. Structural, not
  semantic entailment (spec Flagged concerns). Read route only, and not for an abstention (its next steps are
  fixed text with nothing to cite).
- `tool_success`: 1 if every MCP call returned OK. Not sent when no tool was called.
- `policy_pass`: 1 if policy_check removed, downgraded, rewrote or redacted nothing (rules 1-5, 7). Rule 6 adds a
  version-conflict note and rule 8 an abstention; neither corrects a claim. Refusals and clarifications score 1.
- `answer_echo`: 1 if the composed answer contained instruction-shaped text before fencing (step 8 sets it).
  Not sent before fencing exists, so a 0 is never reported that wasn't measured.
- `incident_event`: 1 per MCP call whose outcome is APPROVAL_*, AUDIT_UNAVAILABLE or INTERNAL_ERROR, with the
  code as the comment. Sent only when it happens.
"""

from __future__ import annotations

from typing import Any

from app.agent import answer

CORRECTING_RULES = frozenset({1, 2, 3, 4, 5, 7})
INCIDENT_CODES = frozenset({"AUDIT_UNAVAILABLE", "INTERNAL_ERROR"})

Score = tuple[str, int, str | None]          # (name, value, comment code)


def is_incident(outcome: str | None) -> bool:
    return bool(outcome) and (outcome.startswith("APPROVAL_") or outcome in INCIDENT_CODES)


def citation_valid(composed: dict[str, Any], retrieved: set[str]) -> int:
    cited = [c for kind in ("findings", "next_steps") for item in composed.get(kind, []) or []
             for c in item.get("citation_ids", []) or []]
    return int(all(c in retrieved for c in cited))


def answer_supported(final: dict[str, Any], results: list[dict[str, Any]]) -> int:
    for f in final.get("findings", []) or []:
        cites, refs = f.get("citation_ids") or [], f.get("validator_refs") or []
        if not cites and not refs:
            return 0
        if f.get("status") == "confirmed" and not cites and not any(
                0 <= r < len(results) and answer.decisive(results[r]) for r in refs):
            return 0
    return int(all(s.get("citation_ids") for s in final.get("next_steps", []) or []))


def policy_pass(actions: list[dict[str, Any]]) -> int:
    return int(not any(a.get("rule") in CORRECTING_RULES for a in actions or []))


def tool_scores(calls: list[dict[str, Any]]) -> list[Score]:
    if not calls:
        return []
    out: list[Score] = [("tool_success", int(all(c.get("outcome") == "OK" for c in calls)), None)]
    out += [("incident_event", 1, c["outcome"]) for c in calls if is_incident(c.get("outcome"))]
    return out


def compute(state: dict[str, Any], *, since_call: int = 0, answer_scores: bool = True) -> list[Score]:
    """Every score for a run. A resumed run passes `since_call` (the calls it made itself) and
    `answer_scores=False`: its answer scores were sent with the first run."""
    out: list[Score] = []
    actions = state.get("policy_actions") or []
    if answer_scores:
        composed = state.get("composed")
        if state.get("route") == "read" and composed is not None:
            retrieved = {c["citation_id"] for c in state.get("reranked_chunks") or []}
            out.append(("citation_valid", citation_valid(composed, retrieved), None))
            if not any(a.get("rule") == 8 for a in actions):
                out.append(("answer_supported", answer_supported(state.get("final_answer") or {},
                                                                 state.get("validator_results") or []), None))
        out.append(("policy_pass", policy_pass(actions), None))
        if state.get("answer_echo") is not None:
            out.append(("answer_echo", int(bool(state["answer_echo"])), None))
    out += tool_scores((state.get("tool_calls") or [])[since_call:])
    return out
