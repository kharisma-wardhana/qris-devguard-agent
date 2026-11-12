"""The LangGraph agent (TRD §11.2-11.4; p5 + p6 specs).

  sanitize_input -> classify_intent -> classify_risk
     prohibited -> finalize (refusal)
     high, no approval store (P5 mode) -> compose_diagnosis (draft only, no tools) -> policy_check -> ... -> finalize
     low, or high with an approval store (P6)
                -> retrieve_evidence -> rerank_evidence -> validate_evidence
                     version mismatch -> finalize (clarification)
                   -> select_tools
                     missing argument -> finalize (clarification)
                   -> execute_read_tools -> compose_diagnosis -> policy_check
                     high (escalate) -> draft_action -> request_human_approval [interrupt]
                                        -> execute_approved_action -> memory_decision -> finalize
                     otherwise       -> memory_decision -> finalize

No model chooses a node, a tool or a tool argument. The model only writes prose in compose_diagnosis.
draft_action creates the approval (idempotent) and is checkpointed before request_human_approval interrupts;
on resume only the interrupting node re-runs. execute_approved_action reads the decision from the database
(authoritative), never from the resume payload, and the MCP tool enforces exactly-once by idempotency key.
"""

from __future__ import annotations

import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import anyio
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.agent import answer, classify, config, fence, policy
from app.agent.llm import compose
from app.agent.prompts import PROMPT_VERSION, SYSTEM_PROMPT, build_user_turn
from app.agent.sanitize import redact_text, sanitize
from app.agent.state import DevGuardState
from app.agent.tools import ToolClient
from app.agent.tools import current_run_id as tool_run_id
from app.mcp_server import config as mcp_config
from app.memory import policy as mpolicy
from app.observability.tracing import NOOP, corpus_version, test_delay_ms

Node = Callable[[DevGuardState], Awaitable[dict[str, Any]]]


def _timed(name: str, fn: Node, agent: Any) -> Node:
    """Times a node into the state's trace dict and (p8) wraps it in a tracing span with the node name and ms only.
    The test-only delay (DEVGUARD_TEST_DELAY_MS, capped; 0 by default) is applied inside classify_intent."""
    async def wrapper(state: DevGuardState) -> dict[str, Any]:
        started = time.perf_counter()
        with agent.tracer.span(name, node=name) as span:
            if name == "classify_intent":
                delay = test_delay_ms()
                if delay:
                    await anyio.sleep(delay / 1000)
            update = await fn(state)
            span.set(ms=int((time.perf_counter() - started) * 1000))
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
    def __init__(self, tools: ToolClient, llm: Any, checkpointer: Any | None = None,
                 db_conninfo: str | None = None, tracer: Any = None) -> None:
        self.tools = tools
        self.llm = llm
        self.tracer = tracer or NOOP                       # p8: app.observability.tracing; no-op when off
        if hasattr(tools, "tracer"):
            tools.tracer = self.tracer
        self.db_conninfo = db_conninfo                     # p6: approval store; None = P5 draft-only mode
        self.checkpointer = checkpointer or InMemorySaver()
        self.graph = self._build().compile(checkpointer=self.checkpointer)

    # --- nodes --------------------------------------------------------------------------------

    async def sanitize_input(self, s: DevGuardState) -> dict[str, Any]:
        msg, ctx, found = sanitize(s["user_query"], s.get("sanitized_context") or {})
        family = ctx.get("api_family")
        if not family and ctx.get("endpoint_id") in mcp_config.mcp()["validators"]["endpoints"]:
            family = mcp_config.mcp()["validators"]["endpoints"][ctx["endpoint_id"]]["api_family"]
        # run() already sanitized (raw input must never reach a checkpoint); this second pass is a safety net and
        # keeps the flag from the first pass.
        return {"user_query": msg, "sanitized_context": ctx, "secret_detected": bool(found) or bool(s.get("secret_detected")),
                "api_family": family, "requested_spec_version": ctx.get("spec_version") or classify.extract_version(msg),
                "approval_status": "not_required", "draft_action": None, "idempotency_key": None, "error": None,
                "limitations": [], "policy_actions": [], "tool_calls": [], "validator_results": [],
                "retrieved_chunks": [], "reranked_chunks": [], "evidence_flags": {}, "clarification": [],
                "planned_calls": [], "escalate": False, "approval": None, "action_result": None,
                "patterns_shown": [], "memory_decision": None, "answer_echo": None}       # p8: per run, never carried from the last run

    async def classify_intent(self, s: DevGuardState) -> dict[str, Any]:
        return {"intent": classify.classify_intent(s["user_query"], s["sanitized_context"])}

    async def classify_risk(self, s: DevGuardState) -> dict[str, Any]:
        risk = classify.classify_risk(s["user_query"])
        if risk == "high" and self.db_conninfo and s.get("hitl", True):   # p6: diagnose, then ask a human
            return {"risk_level": risk, "route": "read", "escalate": True}
        return {"risk_level": risk, "route": {"prohibited": "refuse", "high": "draft"}.get(risk, "read")}

    async def retrieve_evidence(self, s: DevGuardState) -> dict[str, Any]:
        args: dict[str, Any] = {"query": s["user_query"][:500], "top_k": config.agent()["retrieval"]["top_k"]}
        if s.get("api_family") in _family_docs():
            args["api_family"] = s["api_family"]
        tool_run_id.set(s.get("run_id"))
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
        tool_run_id.set(s.get("run_id"))
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
        notes += mpolicy.note_lines(s.get("preferences") or {})        # p8: trusted fixed text from configs
        patterns = await self._patterns(s)
        # p8 (spec Behaviour §4): marker sentences replaced in the prompt only; `sources` keep the original quote
        prompt_evidence, fenced_in = fence.fence_evidence(evidence)
        user = build_user_turn(s["user_query"], prompt_evidence, results, notes, fence.fence_patterns(patterns))
        with self.tracer.generation("llm.compose", model=config.served_model_name(),
                                    prompt_version=PROMPT_VERSION) as gen:
            res = await compose(self.llm, SYSTEM_PROMPT, user, {"evidence": prompt_evidence,
                                                                "validator_results": results})
            gen.usage(res.usage.get("prompt_tokens", 0), res.usage.get("completion_tokens", 0))
            gen.set(attempts=res.attempts, fallback=res.data is None, ms=res.ms, error_code=res.error)
        trace = {"llm": {"prompt_version": PROMPT_VERSION, "attempts": res.attempts, "ms": res.ms, **res.usage,
                         "error": res.error}}
        shown: dict[str, Any] = {"patterns_shown": [str(p["memory_id"]) for p in patterns]}
        if patterns:
            shown["limitations"] = [*s.get("limitations", []), answer.PATTERNS_SHOWN]
        if res.data is None:                             # code-built fallback: no model text to echo
            trace["fence"] = {"evidence_sentences": fenced_in, "answer_sentences": 0}
            return {"composed": answer.fallback(results), "evidence_flags": {**flags, "fallback": True}, "trace": trace,
                    "answer_echo": False, **shown}
        echo = fence.echoes(res.data)                    # measured before the answer fence (answer_echo score)
        composed, removed = fence.fence_answer(res.data)
        trace["fence"] = {"evidence_sentences": fenced_in, "answer_sentences": removed}
        return {"composed": composed, "trace": trace, "answer_echo": echo, **shown}

    async def _patterns(self, s: DevGuardState) -> list[dict[str, Any]]:
        """p8: active verified patterns tagged with this run's API family or intent. No store, or a database
        error, shows none: a diagnosis never fails because of memory."""
        if not self.db_conninfo:
            return []
        from app.memory import store as memory
        try:
            return await self._db(lambda conn: memory.patterns_for_run(conn, api_family=s.get("api_family"),
                                                                       intent=s.get("intent")))
        except Exception:  # noqa: BLE001 - memory is optional for a diagnosis
            return []

    async def policy_check(self, s: DevGuardState) -> dict[str, Any]:
        if s["route"] != "read":                         # fixed answers (draft) carry no generated claims
            return {}
        code = s["sanitized_context"].get("response_code") or classify.extract_code(s["user_query"])
        final, actions = policy.check(s["composed"], evidence=s["reranked_chunks"], results=s["validator_results"],
                                      flags=s["evidence_flags"], code=code)
        return {"final_answer": final, "policy_actions": actions}

    # --- p6: approval and the one side effect ---------------------------------------------------

    async def _db(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        from app.db import store

        def call() -> Any:
            with store.connect(self.db_conninfo) as conn:
                return fn(conn, *args, **kwargs)
        return await anyio.to_thread.run_sync(call)

    def _ticket_payload(self, s: DevGuardState) -> dict[str, Any]:
        lim = config.agent()["ticket_draft"]
        final = s.get("final_answer") or {}
        first = next((ln.strip() for ln in s["user_query"].splitlines() if ln.strip()), "support request")
        cites = sorted({src["citation_id"] for src in final.get("sources", [])})
        vals = [f"VAL-{i}" for i, r in enumerate(s["validator_results"]) if answer.decisive(r)]
        # "tool -> result", not "tool: result": `validate_test_signature: X` reads as a `signature: value` secret pair
        checks = "; ".join(f"{v['tool']} -> {v['result']}" for v in final.get("validation_results", [])) or "none"
        body = (f"Question: {s['user_query']}\n\nDiagnosis: {final.get('summary', '')}\n\nValidation: {checks}")
        # The tool refuses secret-looking text; redact with the same rules before hashing so the approved payload
        # is exactly what the tool will accept and store.
        title = redact_text(f"Escalation: {first}"[: lim["title_max_chars"]])[0]
        return {"title": title[: lim["title_max_chars"]], "body": redact_text(body[: lim["body_max_chars"]])[0][: lim["body_max_chars"]],
                "evidence_ids": (cites + vals)[: lim["max_evidence_ids"]], "priority": lim["default_priority"]}

    async def draft_action(self, s: DevGuardState) -> dict[str, Any]:
        """Draft the ticket and create its pending approval (idempotent), or stay draft-only for a role that may
        not use create_support_ticket. Checkpointed before request_human_approval interrupts."""
        from app.db import actions, store

        payload = self._ticket_payload(s)
        if s["role"] not in mcp_config.tool_roles(actions.ACTION_TYPE):
            final = dict(s.get("final_answer") or {})
            final["limitations"] = [*final.get("limitations", []),
                                    "Your role may not create sandbox tickets; this is a draft only."]
            final["draft"] = {"action": actions.ACTION_TYPE, "status": "draft_only", "payload": actions.normalize(payload)}
            return {"final_answer": final, "escalate": False}
        ttl = mcp_config.security()["approval_ttl_minutes"]
        row = await self._db(store.get_or_create_pending, thread_id=s["thread_id"], run_id=s["run_id"],
                             requester_id=s["user_id"], requester_role=s["role"], payload=payload, ttl_minutes=ttl)
        await self._db(store.set_thread, s["thread_id"], "awaiting_approval", s["run_id"])
        approval = {"approval_id": str(row["approval_id"]), "status": row["status"],
                    "expires_at": row["expires_at"].isoformat(), "action_hash": row["action_hash"],
                    "action": {"type": actions.ACTION_TYPE, "target": "local sandbox_tickets table",
                               "payload": dict(row["action_payload"])}}
        return {"approval": approval, "approval_status": "pending", "draft_action": approval["action"]}

    async def request_human_approval(self, s: DevGuardState) -> dict[str, Any]:
        # Pauses here until /approvals/{id}/decision (or /threads/{id}/resume) resumes the thread. The resume
        # value is ignored: execute_approved_action reads the decision from the database.
        interrupt({"approval_id": s["approval"]["approval_id"], "action": s["approval"]["action"]})
        return {"approval_status": "decided"}

    async def execute_approved_action(self, s: DevGuardState) -> dict[str, Any]:
        from app.db import actions, store

        appr = await self._db(store.get_approval, s["approval"]["approval_id"])
        status = appr["status"] if appr else "missing"
        result: dict[str, Any] = {"approval_status": status}
        calls = list(s["tool_calls"])
        if status in ("approved", "executed"):                # executed: a crash after the insert; tool says so
            payload = dict(appr["action_payload"])
            args = {"approval_id": str(appr["approval_id"]), "thread_id": s["thread_id"], **payload,
                    "idempotency_key": actions.idempotency_key(s["thread_id"], payload)}
            tool_run_id.set(s.get("run_id"))
            (res,) = await self.tools.call(s["user_id"], s["role"], [(actions.ACTION_TYPE, args)])
            calls.append({"tool": res.tool, "outcome": res.error_code or "OK", "ms": res.ms})
            result.update({"status": res.data.get("status") if res.ok else res.error_code,
                           "ticket_id": res.data.get("ticket_id") if res.ok else None})
        else:
            result["status"] = {"rejected": "rejected", "expired": "expired"}.get(status, "not_executed")
        await self._db(store.set_thread, s["thread_id"], "completed", s["run_id"])
        return {"action_result": result, "tool_calls": calls, "approval_status": status}

    async def memory_decision(self, s: DevGuardState) -> dict[str, Any]:
        """TRD §16 / p8 spec Behaviour §1: default no save. Only a configured explicit phrase saves an allowed
        preference, never when the run's input held a secret; the model takes no part. Patterns are never
        saved here (they go through the review routes)."""
        reason, hit = mpolicy.decide(s["user_query"], route=s["route"], secret_detected=bool(s.get("secret_detected")))
        update: dict[str, Any] = {"memory_decision": reason}
        if reason == mpolicy.NOT_UNRECOGNISED:
            update["limitations"] = [*s.get("limitations", []), mpolicy.saveable_hint()]
        if hit is None:
            return update
        if not self.db_conninfo:
            return {"memory_decision": mpolicy.NOT_STORE}
        from app.memory import store as memory
        key, value = hit
        try:
            reason, _ = await self._db(memory.save_preference, s["user_id"], key, value, run_id=s["run_id"],
                                       thread_id=s["thread_id"])
        except Exception:  # noqa: BLE001 - a failed save never fails the diagnosis
            return {"memory_decision": mpolicy.NOT_STORE}
        return {"memory_decision": reason, "preferences": {**(s.get("preferences") or {}), key: value}}

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
        if s.get("action_result"):
            final = answer.with_action(final, s["approval"], s["action_result"])
        if s.get("secret_detected"):
            final["limitations"] = [answer.KEY_WARNING, *final.get("limitations", [])]
        return {"final_answer": final, "memory_decision": s.get("memory_decision") or mpolicy.NOT_ROUTE}

    # --- wiring -------------------------------------------------------------------------------

    def _build(self) -> StateGraph:
        g = StateGraph(DevGuardState)
        names = ["sanitize_input", "classify_intent", "classify_risk", "retrieve_evidence", "rerank_evidence",
                 "validate_evidence", "select_tools", "execute_read_tools", "compose_diagnosis", "policy_check",
                 "draft_action", "request_human_approval", "execute_approved_action", "memory_decision", "finalize"]
        for n in names:
            g.add_node(n, _timed(n, getattr(self, n), self))
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
        g.add_conditional_edges("policy_check", lambda s: "draft_action" if s.get("escalate") else "memory_decision",
                                {"draft_action": "draft_action", "memory_decision": "memory_decision"})
        g.add_conditional_edges("draft_action", lambda s: "request_human_approval" if s.get("approval") else "memory_decision",
                                {"request_human_approval": "request_human_approval", "memory_decision": "memory_decision"})
        g.add_edge("request_human_approval", "execute_approved_action")
        g.add_edge("execute_approved_action", "memory_decision")
        g.add_edge("memory_decision", "finalize")
        g.add_edge("finalize", END)
        return g

    @staticmethod
    def _result(out: dict[str, Any], started: float) -> dict[str, Any]:
        trace = {**out.get("trace", {}), "total_ms": int((time.perf_counter() - started) * 1000)}
        waiting = bool(out.get("__interrupt__"))
        return {"run_id": out["run_id"], "thread_id": out["thread_id"], "intent": out["intent"],
                "risk_level": out["risk_level"], "route": out["route"],
                "status": "awaiting_approval" if waiting else "completed",
                "approval": out.get("approval"), "action_result": out.get("action_result"),
                "answer": out["final_answer"], "tool_calls": out.get("tool_calls", []),
                "policy_actions": out.get("policy_actions", []), "evidence_flags": out.get("evidence_flags", {}),
                "memory_decision": out.get("memory_decision"), "answer_echo": out.get("answer_echo"), "trace": trace}

    async def run(self, *, sub: str, role: str, message: str, context: dict[str, Any] | None = None,
                  thread_id: str | None = None, hitl: bool = True) -> dict[str, Any]:
        """A new run on `thread_id` (or a new thread). With an approval store the thread row is created if absent;
        ownership is checked by the caller (API) before this is called."""
        thread_id, run_id = thread_id or secrets.token_urlsafe(24), str(uuid.uuid4())
        if self.db_conninfo:
            from app.db import store

            def ensure(conn: Any) -> None:
                if store.thread_owner(conn, thread_id) is None:
                    store.create_thread(conn, thread_id, sub)
                store.set_thread(conn, thread_id, "running", run_id)
            await self._db(ensure)
        # Sanitize BEFORE the graph: LangGraph checkpoints the raw input before any node runs, so an unsanitized
        # message would be persisted in the first checkpoint and its writes (p6 step 7 finding).
        msg, ctx, found = sanitize(message, context or {})
        state: DevGuardState = {"thread_id": thread_id, "run_id": run_id, "user_id": sub, "role": role,
                                "user_query": msg, "sanitized_context": ctx, "secret_detected": bool(found),
                                "trace": {}, "hitl": hitl, "preferences": await self._preferences(sub)}
        started = time.perf_counter()
        with self.tracer.run(run_id, name="agent-run", session_id=thread_id, user_id=sub,
                             tags=[f"prompt:{PROMPT_VERSION}"], **self._trace_meta(thread_id)) as root:
            out = await self.graph.ainvoke(state, config={"configurable": {"thread_id": thread_id}})
            if self.db_conninfo and not out.get("__interrupt__") and not out.get("action_result"):
                from app.db import store
                await self._db(store.set_thread, thread_id, "completed", run_id)
            result = self._result(out, started)
            self._trace_outcome(root, result)
        self._send_scores(run_id, out)
        return result

    def _send_scores(self, seed: str, state: dict[str, Any], **kw: Any) -> None:
        """p8: deterministic scores (app.observability.scores) on the run's trace. Never fails a request."""
        if not self.tracer.enabled:
            return
        from app.observability import scores
        try:
            for name, value, comment in scores.compute(state, **kw):
                self.tracer.score_run(seed, name, value, comment=comment)
        except Exception:  # noqa: BLE001 - tracing never fails a request
            return

    @staticmethod
    def _trace_meta(thread_id: str) -> dict[str, Any]:
        model, revision = config.model_identity()
        return {"thread_id": thread_id, "model": model, "model_revision": revision, "prompt_version": PROMPT_VERSION,
                "corpus_version": corpus_version()}

    @staticmethod
    def _trace_outcome(root: Any, result: dict[str, Any]) -> None:
        root.set(intent=result["intent"], risk=result["risk_level"], route=result["route"], status=result["status"],
                 memory_decision=result.get("memory_decision"), ms=result["trace"].get("total_ms"),
                 approval_status=(result.get("approval") or {}).get("status"))

    async def _preferences(self, sub: str) -> dict[str, str]:
        """p8: the caller's own active preferences (owner-scoped, T11). No store, or a database error: none,
        and the run goes on."""
        if not self.db_conninfo:
            return {}
        from app.memory import store as memory
        try:
            return await self._db(memory.get_preferences, sub)
        except Exception:  # noqa: BLE001
            return {}

    async def resume(self, thread_id: str) -> dict[str, Any]:
        """Continue an interrupted run (after a decision, or after a crash). Exactly-once is enforced by the tool."""
        started = time.perf_counter()
        cfg = {"configurable": {"thread_id": thread_id}}
        snap = await self.graph.aget_state(cfg)
        before = (snap.values if snap else None) or {}
        run_id, sub = str(before.get("run_id") or thread_id), str(before.get("user_id") or "-")
        # p8: the resumed run is its own trace (seed run_id:resume), linked to the first by session = thread_id.
        with self.tracer.run(run_id, name="agent-resume", session_id=thread_id, user_id=sub, seed=f"{run_id}:resume",
                             tags=[f"prompt:{PROMPT_VERSION}"], resumed=True, **self._trace_meta(thread_id)) as root:
            out = await self.graph.ainvoke(Command(resume={"resumed": True}), config=cfg)
            await self._approval_wait_span((out.get("approval") or {}).get("approval_id"))
            result = self._result(out, started)
            self._trace_outcome(root, result)
        # only the calls this resume made; the answer scores went with the first run
        self._send_scores(f"{run_id}:resume", out, since_call=len(before.get("tool_calls") or []),
                          answer_scores=False)
        return result

    async def _approval_wait_span(self, approval_id: str | None) -> None:
        """p8: the approval wait (created_at to decided_at) as a span on the resume trace, carried as `ms`: the wait
        spans two processes and possibly a restart, so it is recorded once the decision is known."""
        if not (approval_id and self.db_conninfo and self.tracer.enabled):
            return
        from app.db import store
        try:
            row = await self._db(store.get_approval, approval_id)
        except Exception:  # noqa: BLE001 - tracing never fails a request
            return
        if row and row.get("decided_at") and row.get("created_at"):
            wait = int((row["decided_at"] - row["created_at"]).total_seconds() * 1000)
            with self.tracer.span("approval_wait", ms=wait, approval_status=row.get("status")):
                pass

    async def snapshot(self, thread_id: str) -> dict[str, Any] | None:
        """The thread's latest state: waiting for approval, or the last run's answer. None for an unknown thread."""
        snap = await self.graph.aget_state({"configurable": {"thread_id": thread_id}})
        v = snap.values if snap else None
        if not v:
            return None
        waiting = any(getattr(t, "interrupts", None) for t in (snap.tasks or ()))
        return {"thread_id": thread_id, "run_id": v.get("run_id"),
                "status": "awaiting_approval" if waiting else "completed", "approval": v.get("approval"),
                "action_result": v.get("action_result"), "answer": v.get("final_answer")}
