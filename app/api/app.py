"""FastAPI app: POST /api/v1/chat, /health/live, /health/ready (TRD §18; p5 spec AC #1, #2, #17, #18).

- Bearer auth: HS256 tokens with the API audience (`configs/agent.yaml` api.audience). An MCP-audience token is
  refused here, and the API token is never forwarded to the MCP server (the agent mints its own).
- Every error is the §18 envelope (`code`, `message`, `correlation_id`, optional `field_errors`), with no
  stack trace and no submitted value.
- One log line per request: route, sub, role, intent, risk, outcome, run_id, duration. Never message text.
- p7: a body over `limits.max_request_bytes` gets 413 before parsing; a client address over `limits.rate.per_address`
  gets 429 before authentication; an authenticated subject over its per-route bucket gets 429 (agent-run routes
  `api_agent_run`, everything else `api_other`). 429 carries `Retry-After`. Health routes are not limited.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, FastAPI, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.agent import config
from app.api.models import (PREF_KEY, ChatRequest, ChatResponse, DecisionRequest, FeedbackRequest, MessageResponse,
                            PatternDecision, PatternProposal, PreferenceRequest)
from app.mcp_server.auth import HS256Verifier

log = logging.getLogger("devguard.api")

MESSAGES = {
    "UNAUTHENTICATED": "authentication required",
    "INVALID_ARGUMENT": "one or more fields are invalid",
    "NOT_FOUND": "no such route",
    "METHOD_NOT_ALLOWED": "method not allowed",
    "INTERNAL_ERROR": "the request failed; quote the correlation_id when reporting it",
    # p6
    "FORBIDDEN": "your role or identity may not do this",
    "CONFLICT": "the thread or approval is not in a state that allows this",
    "APPROVAL_EXPIRED": "the approval has expired; nothing was executed",
    "SERVICE_UNAVAILABLE": "the thread and approval store is not available",
    # p7
    "RATE_LIMITED": "too many requests; retry after the number of seconds in Retry-After",
    "PAYLOAD_TOO_LARGE": "the request body is larger than allowed",
}
THREAD_ID = r"^[A-Za-z0-9_-]{16,64}$"
APPROVAL_ID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


def approval_view(row: dict[str, Any]) -> dict[str, Any]:
    """What an approver sees (TRD §15.2): exact action and target, sanitized parameters and evidence, expected
    side effect, requester, expiry, status. Sanitized values only."""
    def iso(v: Any) -> str | None:
        return v.isoformat() if v is not None else None
    payload = dict(row["action_payload"])
    return {"approval_id": str(row["approval_id"]), "thread_id": row["thread_id"], "status": row["status"],
            "action": {"type": row["action_type"], "target": "local sandbox_tickets table", "payload": payload},
            "evidence_ids": payload.get("evidence_ids", []),
            "expected_side_effect": "one row in the local sandbox ticket table; nothing else",
            "requester_id": row["requester_id"], "approver_id": row["approver_id"],
            "decided_at": iso(row["decided_at"]), "expires_at": iso(row["expires_at"]),
            "executed_at": iso(row["executed_at"]), "ticket_id": str(row["ticket_id"]) if row["ticket_id"] else None}


class ApiError(Exception):
    def __init__(self, status: int, code: str, field_errors: list[dict[str, str]] | None = None,
                 headers: dict[str, str] | None = None):
        self.status, self.code, self.field_errors, self.headers = status, code, field_errors or [], headers


def envelope(status: int, code: str, field_errors: list[dict[str, str]] | None = None,
             headers: dict[str, str] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": MESSAGES[code], "correlation_id": uuid.uuid4().hex}
    if field_errors:
        body["field_errors"] = field_errors
    return JSONResponse(body, status_code=status, headers=headers)


def create_app(*, agent: Any, secret: str, mcp_ping: Callable[[], Awaitable[bool]],
               llm_ping: Callable[[], Awaitable[bool]], db_conninfo: str | None = None,
               lifespan: Any = None, limits: dict[str, Any] | None = None) -> FastAPI:
    """`agent` may be None when `lifespan` sets `app.state.agent` (the PostgreSQL checkpointer needs a running
    loop). Without `db_conninfo` the thread and approval routes answer 503 and /chat runs in P5 mode.
    `limits` (tests): TokenBuckets keyed per_address / api_agent_run / api_other; default from security.yaml."""
    from app.security.body_limit import BodyLimit
    from app.security.ratelimit import PerAddressLimit, TokenBucket, retry_after

    given = limits or {}
    buckets = {name: given[name] if given.get(name) is not None else TokenBucket.from_config(name)   # not `or`:
               for name in ("per_address", "api_agent_run", "api_other")}                         # empty = falsy
    verifier = HS256Verifier(secret, audience=config.agent()["api"]["audience"])
    app = FastAPI(title="QRIS DevGuard AI", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    app.state.agent = agent
    app.state.tracer = None                                # p8: set by the server (app/observability)
    approver_roles = set(config.security()["approver_roles"])
    # add_middleware wraps outside-in in reverse order: PerAddressLimit ends up outermost, then BodyLimit
    app.add_middleware(BodyLimit, max_bytes=config.security()["limits"]["max_request_bytes"])
    app.add_middleware(PerAddressLimit, bucket=buckets["per_address"])

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else exc.headers
        return envelope(exc.status, exc.code, exc.field_errors, headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = sorted({(".".join(str(p) for p in e["loc"] if p != "body") or "<body>", str(e["type"]))
                         for e in exc.errors()})
        return envelope(422, "INVALID_ARGUMENT", [{"field": f[:120], "reason": r} for f, r in fields])

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "INVALID_ARGUMENT")
        return envelope(exc.status_code, code)

    @app.exception_handler(Exception)
    async def _crash(_: Request, exc: Exception) -> JSONResponse:
        log.error("internal error type=%s", type(exc).__name__)
        return envelope(500, "INTERNAL_ERROR")

    def identity(request: Request) -> tuple[str, str]:
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        claims = verifier.claims(token.strip()) if scheme.lower() == "bearer" and token else None
        if claims is None:
            raise ApiError(401, "UNAUTHENTICATED")
        return claims["sub"], claims["role"]

    def limited(bucket_name: str) -> Callable[..., tuple[str, str]]:
        def dependency(who: tuple[str, str] = Depends(identity)) -> tuple[str, str]:
            wait = buckets[bucket_name].take(f"sub:{who[0]}")
            if wait > 0:
                log.info("rate_limited bucket=%s sub=%s role=%s", bucket_name, who[0], who[1])
                raise ApiError(429, "RATE_LIMITED", headers={"Retry-After": retry_after(wait)})
            return who
        return dependency

    run_identity = limited("api_agent_run")             # routes that run the agent (LLM + tools)
    other_identity = limited("api_other")

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    async def ready() -> JSONResponse:
        checks = {"mcp": "up" if await mcp_ping() else "down", "llm": "up" if await llm_ping() else "down"}
        ok = all(v == "up" for v in checks.values())
        return JSONResponse({"status": "ready" if ok else "not_ready", "checks": checks}, status_code=200 if ok else 503)

    @app.post("/api/v1/chat", response_model=ChatResponse)
    async def chat(body: ChatRequest, who: tuple[str, str] = Depends(run_identity)) -> dict[str, Any]:
        sub, role = who
        started = time.perf_counter()
        # One-shot and unchanged since P5: no thread routes, so no approval is ever created here (hitl=False);
        # escalation needs a thread (POST /api/v1/threads/{id}/messages).
        out = await app.state.agent.run(sub=sub, role=role, message=body.message, context=body.context_dict(),
                                        hitl=False)
        log.info("route=/api/v1/chat sub=%s role=%s intent=%s risk=%s outcome=%s run_id=%s ms=%d", sub, role,
                 out["intent"], out["risk_level"], out["route"], out["run_id"], (time.perf_counter() - started) * 1000)
        return {k: out[k] for k in ("run_id", "thread_id", "intent", "risk_level", "answer")}

    # --- p6: threads and approvals ---------------------------------------------------------------

    async def db(fn: Callable[..., Any], *args: Any) -> Any:
        if not db_conninfo:
            raise ApiError(503, "SERVICE_UNAVAILABLE")
        import psycopg

        from app.db import store

        def call() -> Any:
            with store.connect(db_conninfo) as conn:
                return fn(conn, *args)
        try:
            return await run_in_threadpool(call)
        except psycopg.Error:
            raise ApiError(503, "SERVICE_UNAVAILABLE") from None

    async def owned_thread(thread_id: str, sub: str) -> dict[str, Any]:
        from app.db import store
        row = await db(store.get_thread, thread_id, sub)
        if row is None:                                    # unknown or someone else's: same answer (T11)
            raise ApiError(404, "NOT_FOUND")
        return row

    def log_route(route: str, sub: str, role: str, outcome: str, started: float, run_id: str | None = None) -> None:
        log.info("route=%s sub=%s role=%s outcome=%s run_id=%s ms=%d", route, sub, role, outcome, run_id or "-",
                 (time.perf_counter() - started) * 1000)

    def message_out(out: dict[str, Any]) -> dict[str, Any]:
        return {k: out.get(k) for k in ("thread_id", "run_id", "status", "intent", "risk_level", "answer", "approval",
                                        "memory_decision")}

    @app.post("/api/v1/threads", status_code=201)
    async def create_thread(who: tuple[str, str] = Depends(other_identity)) -> dict[str, Any]:
        import secrets

        from app.db import store
        sub, role = who
        started, thread_id = time.perf_counter(), secrets.token_urlsafe(24)
        await db(store.create_thread, thread_id, sub)
        log_route("/api/v1/threads", sub, role, "created", started)
        return {"thread_id": thread_id, "status": "idle"}

    @app.post("/api/v1/threads/{thread_id}/messages", response_model=MessageResponse)
    async def post_message(body: ChatRequest, thread_id: str = Path(pattern=THREAD_ID),
                           who: tuple[str, str] = Depends(run_identity)) -> dict[str, Any]:
        sub, role = who
        started = time.perf_counter()
        thread = await owned_thread(thread_id, sub)
        if thread["status"] in ("awaiting_approval", "running"):
            raise ApiError(409, "CONFLICT")
        out = await app.state.agent.run(sub=sub, role=role, message=body.message, context=body.context_dict(),
                                        thread_id=thread_id)
        log_route("/api/v1/threads/{id}/messages", sub, role, out["status"], started, out["run_id"])
        return message_out(out)

    @app.get("/api/v1/threads/{thread_id}")
    async def get_thread(thread_id: str = Path(pattern=THREAD_ID), who: tuple[str, str] = Depends(other_identity)) -> dict[str, Any]:
        sub, _ = who
        thread = await owned_thread(thread_id, sub)
        snap = await app.state.agent.snapshot(thread_id) or {}
        return {"thread_id": thread_id, "status": thread["status"], "run_id": snap.get("run_id"),
                "answer": snap.get("answer"), "approval": snap.get("approval"), "action_result": snap.get("action_result")}

    @app.get("/api/v1/approvals/{approval_id}")
    async def get_approval(approval_id: str = Path(pattern=APPROVAL_ID),
                           who: tuple[str, str] = Depends(other_identity)) -> dict[str, Any]:
        from app.db import store
        sub, role = who
        row = await db(store.get_approval, approval_id)
        if row is None or (row["requester_id"] != sub and role not in approver_roles):
            raise ApiError(404, "NOT_FOUND")
        return approval_view(row)

    @app.post("/api/v1/approvals/{approval_id}/decision")
    async def decide(body: DecisionRequest, approval_id: str = Path(pattern=APPROVAL_ID),
                     who: tuple[str, str] = Depends(other_identity)) -> Any:
        from app.db import store
        sub, role = who
        started = time.perf_counter()
        if role not in approver_roles:                    # spec AC #5: developer / kb_maintainer -> 403
            raise ApiError(403, "FORBIDDEN")
        if await db(store.get_approval, approval_id) is None:
            raise ApiError(404, "NOT_FOUND")
        outcome, row = await db(store.decide, approval_id, sub, body.decision, body.reason)
        if outcome == store.SELF:
            raise ApiError(403, "FORBIDDEN")                # four-eyes: never your own action, even as admin
        if outcome == store.NOT_PENDING:
            raise ApiError(409, "CONFLICT")
        out = await app.state.agent.resume(row["thread_id"])     # expired too: the run ends "nothing was created"
        log_route("/api/v1/approvals/{id}/decision", sub, role, outcome, started, out.get("run_id"))
        if outcome == store.EXPIRED:
            return envelope(409, "APPROVAL_EXPIRED")
        return {"approval": approval_view(await db(store.get_approval, approval_id)), "run": message_out(out)}

    @app.post("/api/v1/threads/{thread_id}/resume")
    async def resume(thread_id: str = Path(pattern=THREAD_ID),
                     who: tuple[str, str] = Depends(run_identity)) -> dict[str, Any]:
        """Finish a run whose approval was decided but whose resume did not complete (crash, restart)."""
        from app.db import store
        sub, role = who
        started = time.perf_counter()
        owner = await db(store.thread_owner, thread_id)
        if owner is None or (owner != sub and role not in approver_roles):
            raise ApiError(404, "NOT_FOUND")
        snap = await app.state.agent.snapshot(thread_id)
        appr = await db(store.latest_approval, thread_id)
        if not snap or snap["status"] != "awaiting_approval" or appr is None or appr["status"] == "pending":
            raise ApiError(409, "CONFLICT")
        out = await app.state.agent.resume(thread_id)
        log_route("/api/v1/threads/{id}/resume", sub, role, out["status"], started, out.get("run_id"))
        return message_out(out)

    # --- p8: long-term memory (spec Behaviour §1) and feedback (§3) --------------------------------

    def iso(v: Any) -> str | None:
        return v.isoformat() if v is not None else None

    def pref_view(row: dict[str, Any]) -> dict[str, Any]:
        return {"key": row["pref_key"], "value": row["content_sanitized"], "version": row["version"],
                "updated_at": iso(row["updated_at"]), "expires_at": iso(row["expires_at"])}

    def pattern_view(row: dict[str, Any]) -> dict[str, Any]:
        return {"pattern_id": str(row["memory_id"]), "title": row["title"], "text": row["content_sanitized"],
                "api_family": row["api_family"], "intent": row["intent"], "status": row["status"],
                "proposed_by": row["proposed_by"], "approved_by": row["approved_by"],
                "expires_at": iso(row["expires_at"])}

    def refuse_unsafe(**fields: str | None) -> None:
        """Pattern text is refused, never redacted, when it looks like a secret or personal data. The field
        errors name the field and the kind, never the value."""
        from app.memory import policy
        errors = [{"field": name, "reason": f"refused:{kind}"}
                  for name, value in fields.items() if value for kind in policy.refusal_kinds(value)]
        if errors:
            raise ApiError(422, "INVALID_ARGUMENT", errors)

    @app.get("/api/v1/memory/preferences")
    async def list_preferences(who: tuple[str, str] = Depends(other_identity)) -> dict[str, Any]:
        from app.memory import store as memory
        rows = await db(memory.list_preferences, who[0])
        return {"preferences": [pref_view(r) for r in rows]}

    @app.put("/api/v1/memory/preferences/{key}")
    async def put_preference(body: PreferenceRequest, key: str = Path(pattern=PREF_KEY),
                             who: tuple[str, str] = Depends(other_identity)) -> dict[str, Any]:
        from app.memory import policy
        from app.memory import store as memory
        sub, role = who
        started = time.perf_counter()
        if not policy.is_allowed(key, body.value):
            raise ApiError(422, "INVALID_ARGUMENT", [{"field": "value", "reason": "not_allowed"}])
        reason, row = await db(memory.save_preference, sub, key, body.value)
        log_route("/api/v1/memory/preferences/{key}", sub, role, reason, started)
        return {**pref_view(row), "memory_decision": reason}

    @app.delete("/api/v1/memory/preferences/{key}", status_code=204)
    async def delete_preference(key: str = Path(pattern=PREF_KEY),
                                who: tuple[str, str] = Depends(other_identity)) -> Response:
        from app.memory import store as memory
        sub, role = who
        started = time.perf_counter()
        if not await db(memory.delete_preference, sub, key):
            raise ApiError(404, "NOT_FOUND")               # never saved, or someone else's: same answer (T11)
        log_route("/api/v1/memory/preferences/{key}", sub, role, "deleted", started)
        return Response(status_code=204)

    @app.get("/api/v1/memory/patterns")
    async def list_patterns(who: tuple[str, str] = Depends(other_identity)) -> dict[str, Any]:
        from app.memory import store as memory
        sub, role = who
        rows = await db(lambda conn: memory.list_patterns(conn, sub, approver=role in approver_roles))
        return {"patterns": [pattern_view(r) for r in rows]}

    @app.post("/api/v1/memory/patterns", status_code=201)
    async def propose_pattern(body: PatternProposal, who: tuple[str, str] = Depends(other_identity)) -> dict[str, Any]:
        from app.memory import store as memory
        sub, role = who
        started = time.perf_counter()
        thread = await owned_thread(body.thread_id, sub)
        refuse_unsafe(title=body.title, text=body.text)
        run_id = str(thread["last_run_id"]) if thread.get("last_run_id") else None
        row = await db(lambda conn: memory.propose_pattern(
            conn, proposer=sub, thread_id=body.thread_id, run_id=run_id, title=body.title, text=body.text,
            api_family=body.api_family, intent=body.intent))
        log_route("/api/v1/memory/patterns", sub, role, "proposed", started)
        return pattern_view(row)

    @app.post("/api/v1/memory/patterns/{pattern_id}/decision")
    async def decide_pattern(body: PatternDecision, pattern_id: str = Path(pattern=APPROVAL_ID),
                             who: tuple[str, str] = Depends(other_identity)) -> dict[str, Any]:
        from app.memory import store as memory
        sub, role = who
        started = time.perf_counter()
        if role not in approver_roles:
            raise ApiError(403, "FORBIDDEN")
        refuse_unsafe(reason=body.reason)
        outcome, row = await db(lambda conn: memory.decide_pattern(
            conn, pattern_id, approver=sub, approve=body.decision == "approve", reason=body.reason))
        if outcome == memory.NOT_FOUND:
            raise ApiError(404, "NOT_FOUND")
        if outcome == memory.SELF:
            raise ApiError(403, "FORBIDDEN")                # four eyes: never your own pattern, even as admin
        if outcome == memory.NOT_PENDING:
            raise ApiError(409, "CONFLICT")
        log_route("/api/v1/memory/patterns/{id}/decision", sub, role, row["status"], started)
        return pattern_view(row)

    @app.delete("/api/v1/memory/patterns/{pattern_id}", status_code=204)
    async def delete_pattern(pattern_id: str = Path(pattern=APPROVAL_ID),
                             who: tuple[str, str] = Depends(other_identity)) -> Response:
        from app.memory import store as memory
        sub, role = who
        started = time.perf_counter()
        outcome = await db(lambda conn: memory.delete_pattern(conn, pattern_id, sub=sub, admin=role == "admin"))
        if outcome == memory.NOT_FOUND:
            raise ApiError(404, "NOT_FOUND")
        if outcome == memory.FORBIDDEN:
            raise ApiError(403, "FORBIDDEN")
        log_route("/api/v1/memory/patterns/{id}", sub, role, "deleted", started)
        return Response(status_code=204)

    @app.post("/api/v1/threads/{thread_id}/feedback", status_code=204)
    async def feedback(body: FeedbackRequest, thread_id: str = Path(pattern=THREAD_ID),
                       who: tuple[str, str] = Depends(other_identity)) -> Response:
        """user_feedback on the thread's last run (spec AC #19). Owner only. Stored only as a trace score."""
        sub, role = who
        started = time.perf_counter()
        thread = await owned_thread(thread_id, sub)
        if not thread.get("last_run_id"):
            raise ApiError(409, "CONFLICT")
        tracer = getattr(app.state, "tracer", None)
        if tracer is not None:
            tracer.score_run(str(thread["last_run_id"]), "user_feedback", body.rating, session_id=thread_id)
        log_route("/api/v1/threads/{id}/feedback", sub, role, body.rating, started, str(thread["last_run_id"]))
        return Response(status_code=204)

    return app


async def http_ping(url: str) -> bool:
    """Any HTTP answer (even 401) means the service is up; a transport error means down."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=3, follow_redirects=False) as c:     # p7: never follow
            await c.post(url, json={})
        return True
    except httpx.HTTPError:
        return False
