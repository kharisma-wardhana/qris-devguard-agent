"""The P5 LangGraph agent (TRD §11.2-11.3; p5 spec Behaviour; AC #5, #6, #11-#14).

  sanitize_input -> classify_intent -> classify_risk
     prohibited -> finalize (refusal)
     high       -> compose_diagnosis (draft only, no tools) -> policy_check -> memory_decision -> finalize
     low        -> retrieve_evidence -> rerank_evidence -> validate_evidence
                     version mismatch -> finalize (clarification)
                   -> select_tools
                     missing argument -> finalize (clarification)
                   -> execute_read_tools -> compose_diagnosis -> policy_check -> memory_decision -> finalize

No model chooses a node, a tool or a tool argument. The model only writes prose in compose_diagnosis.
P5 compiles with InMemorySaver; P6 swaps in the PostgreSQL checkpointer and adds the approval nodes.
"""

from __future__ import annotations

import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.agent import answer, classify, config, policy
from app.agent.llm import compose
from app.agent.prompts import PROMPT_VERSION, SYSTEM_PROMPT, build_user_turn
from app.agent.sanitize import sanitize
from app.agent.state import DevGuardState
from app.agent.tools import ToolClient
from app.mcp_server import config as mcp_config

Node = Callable[[DevGuardState], Awaitable[dict[str, Any]]]


def _timed(name: str, fn: Node) -> Node:
    async def wrapper(state: DevGuardState) -> dict[str, Any]:
        started = time.perf_counter()
        update = await fn(state)
        trace = dict(state.get("trace") or {})
        trace.setdefault("nodes", {})
        trace["nodes"] = {**trace["nodes"], name: int((time.perf_counter() - started) * 1000)}
        if "trace" in update:                              # a node may add its own keys (llm usage)
            trace = {**trace, **{k: v for k, v in update["trace"].items() if k != "nodes"}}
        return {**update, "trace": trace}
    return wrapper


def _family_docs() -> dict[str, list[str]]:
    return mcp_config.mcp()["search"]["api_families"]


def _in_section(section_id: str, prefix: str) -> bool:
    return section_id == prefix or section_id.startswith((prefix + ".", prefix + "#"))


def order_evidence(chunks: list[dict[str, Any]], *, api_family: str | None, endpoint_id: str | None,
                   profile: str | None) -> list[dict[str, Any]]:
    """Stable re-sort of the fused evidence (p6 flow-1 fix; configs/agent.yaml evidence_order):
    0 = the endpoint's own section (endpoint_id, or implied by the signature profile),
    1 = other chunks of the requested API family's documents, 2 = the rest; ties keep fused rank."""
    eo = config.agent()["evidence_order"]
    endpoint = endpoint_id or eo["profile_endpoints"].get(profile or "")
    section = eo["endpoint_sections"].get(endpoint or "")
    family = set(_family_docs().get(api_family or "", []))
    if section:
        family.add(section["document_id"])

    def key(item: tuple[int, dict[str, Any]]) -> tuple[int, int]:
        i, c = item
        if section and c["document_id"] == section["document_id"] and _in_section(str(c.get("section_id", "")),
                                                                                  section["section_prefix"]):
            return 0, i
        return (1 if c["document_id"] in family else 2), i

    return [c for _, c in sorted(enumerate(chunks), key=key)]


class Agent:
    def __init__(self, tools: ToolClient, llm: Any, checkpointer: Any | None = None) -> None:
        self.tools = tools
        self.llm = llm
        self.checkpointer = checkpointer or InMemorySaver()
        self.graph = self._build().compile(checkpointer=self.checkpointer)

    # --- nodes --------------------------------------------------------------------------------

    async def sanitize_input(self, s: DevGuardState) -> dict[str, Any]:
        msg, ctx, found = sanitize(s["user_query"], s.get("sanitized_context") or {})
        family = ctx.get("api_family")
        if not family and ctx.get("endpoint_id") in mcp_config.mcp()["validators"]["endpoints"]:
            family = mcp_config.mcp()["validators"]["endpoints"][ctx["endpoint_id"]]["api_family"]
        return {"user_query": msg, "sanitized_context": ctx, "secret_detected": bool(found),
                "api_family": family, "requested_spec_version": ctx.get("spec_version") or classify.extract_version(msg),
                "approval_status": "not_required", "draft_action": None, "idempotency_key": None, "error": None,
                "limitations": [], "policy_actions": [], "tool_calls": [], "validator_results": [],
                "retrieved_chunks": [], "reranked_chunks": [], "evidence_flags": {}, "clarification": [],
                "planned_calls": []}

    async def classify_intent(self, s: DevGuardState) -> dict[str, Any]:
        return {"intent": classify.classify_intent(s["user_query"], s["sanitized_context"])}

    async def classify_risk(self, s: DevGuardState) -> dict[str, Any]:
        risk = classify.classify_risk(s["user_query"])
        return {"risk_level": risk, "route": {"prohibited": "refuse", "high": "draft"}.get(risk, "read")}

    async def retrieve_evidence(self, s: DevGuardState) -> dict[str, Any]:
        args: dict[str, Any] = {"query": s["user_query"][:500], "top_k": config.agent()["retrieval"]["top_k"]}
        if s.get("api_family") in _family_docs():
            args["api_family"] = s["api_family"]
        (res,) = await self.tools.call(s["user_id"], s["role"], [("search_qris_spec", args)])
        calls = [*s["tool_calls"], {"tool": res.tool, "outcome": res.error_code or "OK", "ms": res.ms}]
        if not res.ok:
            return {"retrieved_chunks": [], "tool_calls": calls,
                    "evidence_flags": {"search_unavailable": True, "search_error": res.error_code}}
        return {"retrieved_chunks": res.data.get("results", []), "tool_calls": calls,
                "evidence_flags": {"reranked": bool(res.data.get("reranked"))}}

    async def rerank_evidence(self, s: DevGuardState) -> dict[str, Any]:
        # Reranking happens inside search_qris_spec when configs/mcp.yaml enables it; this node never runs the
        # CPU cross-encoder itself (p3: ~160 s/query). p6: the fused order is re-sorted so the endpoint's own
        # section, then the requested API family's documents, come first (flow-1 fix); still marked not reranked.
        flags = dict(s["evidence_flags"])
        flags["not_reranked"] = not flags.get("reranked")
        ctx = s["sanitized_context"]
        profile = (ctx.get("signature") or {}).get("canonicalization_profile")
        ordered = order_evidence(s["retrieved_chunks"], api_family=s.get("api_family"),
                                 endpoint_id=ctx.get("endpoint_id"), profile=profile)
        return {"reranked_chunks": ordered, "evidence_flags": flags}

    async def validate_evidence(self, s: DevGuardState) -> dict[str, Any]:
        chunks, flags = s["reranked_chunks"], dict(s["evidence_flags"])
        flags["no_evidence"] = not chunks
        versions: dict[str, set[str]] = {}
        for c in chunks:
            versions.setdefault(c["document_id"], set()).add(c["document_version"])
        flags["version_conflict"] = any(len(v) > 1 for v in versions.values())
        wanted = s.get("requested_spec_version")
        held = sorted({c["document_version"] for c in chunks})
        if wanted and chunks and wanted not in held:
            flags["version_mismatch"] = {"requested": wanted, "available": held}
            return {"evidence_flags": flags, "route": "clarify"}
        return {"evidence_flags": flags}

    async def select_tools(self, s: DevGuardState) -> dict[str, Any]:
        """The intent's allowlisted validator, only when one was requested (a context block, or a code in the
        message). A partly filled request -> clarification. No request -> documentation-only answer."""
        ctx, intent = s["sanitized_context"], s["intent"]
        missing: list[str] = []
        planned: list[list[Any]] = []
        if intent == "signature" and ctx.get("signature"):
            sig = ctx["signature"]
            need = ("algorithm", "fixture_id", "canonicalization_profile", "test_case_ref")
            missing = [f"signature.{k}" for k in need if not sig.get(k)]
            if not missing:
                planned.append(["validate_test_signature", {
                    "algorithm": sig["algorithm"], "fixture_id": sig["fixture_id"],
                    "canonicalization_profile": sig["canonicalization_profile"], "test_payload_ref": sig["test_case_ref"]}])
        elif intent in ("payload", "timestamp") and ctx.get("payload"):
            need = {"endpoint_id": ctx.get("endpoint_id"), "spec_version": ctx.get("spec_version"),
                    "api_family": s.get("api_family")}
            missing = [k for k, v in need.items() if not v]
            if not missing:
                planned.append(["validate_request_payload", {"api_family": s["api_family"], "endpoint_id": ctx["endpoint_id"],
                                                             "spec_version": ctx["spec_version"], "payload": ctx["payload"]}])
        elif intent == "response_code":
            code = ctx.get("response_code") or classify.extract_code(s["user_query"])
            if code:
                need = {"api_family": s.get("api_family"), "spec_version": ctx.get("spec_version")}
                missing = [k for k, v in need.items() if not v]
                if not missing:
                    planned.append(["lookup_response_code", {"code": code, "api_family": s["api_family"],
                                                             "spec_version": ctx["spec_version"]}])
        if missing:
            return {"clarification": missing, "route": "clarify"}
        limitations = list(s.get("limitations", []))
        if not planned and intent in ("signature", "payload", "timestamp", "response_code"):
            limitations.append("No validator was run: no test case, payload or response code was supplied, so this "
                               "answer comes from the documentation only.")
        return {"planned_calls": planned, "limitations": limitations}

    async def execute_read_tools(self, s: DevGuardState) -> dict[str, Any]:
        calls = [(name, args) for name, args in s.get("planned_calls", [])]
        results = await self.tools.call(s["user_id"], s["role"], calls)
        return {"validator_results": [{"tool": r.tool, "ok": r.ok, "data": r.data} for r in results],
                "tool_calls": [*s["tool_calls"], *({"tool": r.tool, "outcome": r.error_code or "OK", "ms": r.ms}
                                                   for r in results)],
                "planned_calls": []}                        # arguments (sanitized payloads) do not stay in state

    async def compose_diagnosis(self, s: DevGuardState) -> dict[str, Any]:
        if s["route"] == "draft":
            return {"final_answer": answer.draft_only(s["user_query"])}
        flags = s["evidence_flags"]
        notes = []
        if flags.get("version_conflict"):
            notes.append("Evidence contains more than one version of the same document; keep them apart.")
        if flags.get("not_reranked"):
            notes.append("Evidence is in fused retrieval order (not reranked).")
        if flags.get("search_unavailable"):
            notes.append("Documentation search was unavailable; only validator results are available.")
        evidence = s["reranked_chunks"]
        results = s["validator_results"]
        user = build_user_turn(s["user_query"], evidence, results, notes)
        res = await compose(self.llm, SYSTEM_PROMPT, user, {"evidence": evidence, "validator_results": results})
        trace = {"llm": {"prompt_version": PROMPT_VERSION, "attempts": res.attempts, "ms": res.ms, **res.usage,
                         "error": res.error}}
        if res.data is None:
            return {"composed": answer.fallback(results), "evidence_flags": {**flags, "fallback": True}, "trace": trace}
        return {"composed": res.data, "trace": trace}

    async def policy_check(self, s: DevGuardState) -> dict[str, Any]:
        if s["route"] != "read":                         # fixed answers (draft) carry no generated claims
            return {}
        code = s["sanitized_context"].get("response_code") or classify.extract_code(s["user_query"])
        final, actions = policy.check(s["composed"], evidence=s["reranked_chunks"], results=s["validator_results"],
                                      flags=s["evidence_flags"], code=code)
        return {"final_answer": final, "policy_actions": actions}

    async def memory_decision(self, s: DevGuardState) -> dict[str, Any]:
        return {"memory_decision": "not_saved"}          # TRD §16 default; the policy arrives in P8

    async def finalize(self, s: DevGuardState) -> dict[str, Any]:
        route, final = s["route"], s.get("final_answer")
        if route == "refuse":
            final = answer.refusal()
        elif route == "clarify":
            mm = s["evidence_flags"].get("version_mismatch")
            if mm:
                final = answer.clarification(["spec_version"], f"The documents found are version {', '.join(mm['available'])}; "
                                                               f"version {mm['requested']} is not in the knowledge base. "
                                                               "Which version do you integrate against?")
            else:
                final = answer.clarification(s["clarification"])
        final = dict(final or answer.abstention("No answer was produced."))
        if route == "read" and s.get("limitations"):
            final["limitations"] = list(dict.fromkeys([*final.get("limitations", []), *s["limitations"]]))
        if s.get("secret_detected"):
            final["limitations"] = [answer.KEY_WARNING, *final.get("limitations", [])]
        return {"final_answer": final, "memory_decision": s.get("memory_decision") or "not_saved"}

    # --- wiring -------------------------------------------------------------------------------

    def _build(self) -> StateGraph:
        g = StateGraph(DevGuardState)
        names = ["sanitize_input", "classify_intent", "classify_risk", "retrieve_evidence", "rerank_evidence",
                 "validate_evidence", "select_tools", "execute_read_tools", "compose_diagnosis", "policy_check",
                 "memory_decision", "finalize"]
        for n in names:
            g.add_node(n, _timed(n, getattr(self, n)))
        g.add_edge(START, "sanitize_input")
        g.add_edge("sanitize_input", "classify_intent")
        g.add_edge("classify_intent", "classify_risk")
        g.add_conditional_edges("classify_risk", lambda s: s["route"],
                                {"refuse": "finalize", "draft": "compose_diagnosis", "read": "retrieve_evidence"})
        g.add_edge("retrieve_evidence", "rerank_evidence")
        g.add_edge("rerank_evidence", "validate_evidence")
        g.add_conditional_edges("validate_evidence", lambda s: s["route"], {"clarify": "finalize", "read": "select_tools"})
        g.add_conditional_edges("select_tools", lambda s: s["route"], {"clarify": "finalize", "read": "execute_read_tools"})
        g.add_edge("execute_read_tools", "compose_diagnosis")
        g.add_edge("compose_diagnosis", "policy_check")
        g.add_edge("policy_check", "memory_decision")
        g.add_edge("memory_decision", "finalize")
        g.add_edge("finalize", END)
        return g

    async def run(self, *, sub: str, role: str, message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        thread_id, run_id = secrets.token_urlsafe(24), str(uuid.uuid4())
        state: DevGuardState = {"thread_id": thread_id, "run_id": run_id, "user_id": sub, "role": role,
                                "user_query": message, "sanitized_context": context or {}, "trace": {}}
        started = time.perf_counter()
        out = await self.graph.ainvoke(state, config={"configurable": {"thread_id": thread_id}})
        trace = {**out.get("trace", {}), "total_ms": int((time.perf_counter() - started) * 1000)}
        return {"run_id": run_id, "thread_id": thread_id, "intent": out["intent"], "risk_level": out["risk_level"],
                "route": out["route"], "answer": out["final_answer"], "tool_calls": out.get("tool_calls", []),
                "policy_actions": out.get("policy_actions", []), "evidence_flags": out.get("evidence_flags", {}),
                "memory_decision": out.get("memory_decision"), "trace": trace}
