"""Graph state (TRD §11.1 plus the p5 spec's extra fields).

State holds sanitized text, redacted context, fixture IDs, citation metadata and validator results only.
Nothing here may hold a raw secret, key or signature (CLAUDE.md invariant).
"""

from __future__ import annotations

from typing import Any, TypedDict

INTENTS = ("signature", "payload", "timestamp", "response_code", "version_question", "general_troubleshooting")
RISKS = ("low", "high", "prohibited")
FINDING_STATUSES = ("confirmed", "likely", "unverified")


class DevGuardState(TypedDict, total=False):
    # TRD §11.1
    thread_id: str
    user_id: str
    user_query: str                  # sanitized message
    sanitized_context: dict[str, Any]
    intent: str
    risk_level: str
    api_family: str | None
    requested_spec_version: str | None
    retrieved_chunks: list[dict[str, Any]]   # citation metadata + capped excerpts from search_qris_spec
    reranked_chunks: list[dict[str, Any]]
    validator_results: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]         # {tool, outcome, ms} - no arguments
    draft_action: dict[str, Any] | None
    approval_status: str | None              # not_required in P5
    final_answer: dict[str, Any] | None
    error: dict[str, Any] | None
    idempotency_key: str | None
    # p5 additions
    run_id: str
    role: str
    route: str                               # read | refuse | draft | clarify
    clarification: list[str]
    planned_calls: list[list[Any]]           # [tool, args] chosen by select_tools; cleared after execution
    # p6
    escalate: bool                           # high risk with an approval store: draft a ticket after diagnosis
    hitl: bool                               # False for the one-shot /chat: high risk stays draft-only (P5)
    approval: dict[str, Any] | None          # approval_id, status, expires_at, action (type, target, payload)
    action_result: dict[str, Any] | None     # status (created | already_created | rejected | expired | <code>)
    secret_detected: bool
    evidence_flags: dict[str, Any]
    limitations: list[str]
    composed: dict[str, Any] | None          # model (or fallback) output before policy_check
    policy_actions: list[dict[str, Any]]
    memory_decision: str
    trace: dict[str, Any]                    # node timings (ms), LLM usage
    # p8
    preferences: dict[str, str]              # the caller's active preferences: allowed keys and enum values only
    patterns_shown: list[str]                # memory IDs of verified patterns shown in the prompt (never their text)
