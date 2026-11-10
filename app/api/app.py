"""FastAPI app: POST /api/v1/chat, /health/live, /health/ready (TRD §18; p5 spec AC #1, #2, #17, #18).

- Bearer auth: HS256 tokens with the API audience (`configs/agent.yaml` api.audience). An MCP-audience token is
  refused here, and the API token is never forwarded to the MCP server (the agent mints its own).
- Every error is the §18 envelope (`code`, `message`, `correlation_id`, optional `field_errors`), with no
  stack trace and no submitted value.
- One log line per request: route, sub, role, intent, risk, outcome, run_id, duration. Never message text.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.agent import config
from app.api.models import ChatRequest, ChatResponse
from app.mcp_server.auth import HS256Verifier

log = logging.getLogger("devguard.api")

MESSAGES = {
    "UNAUTHENTICATED": "authentication required",
    "INVALID_ARGUMENT": "one or more fields are invalid",
    "NOT_FOUND": "no such route",
    "METHOD_NOT_ALLOWED": "method not allowed",
    "INTERNAL_ERROR": "the request failed; quote the correlation_id when reporting it",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, field_errors: list[dict[str, str]] | None = None):
        self.status, self.code, self.field_errors = status, code, field_errors or []


def envelope(status: int, code: str, field_errors: list[dict[str, str]] | None = None,
             headers: dict[str, str] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": MESSAGES[code], "correlation_id": uuid.uuid4().hex}
    if field_errors:
        body["field_errors"] = field_errors
    return JSONResponse(body, status_code=status, headers=headers)


def create_app(*, agent: Any, secret: str, mcp_ping: Callable[[], Awaitable[bool]],
               llm_ping: Callable[[], Awaitable[bool]]) -> FastAPI:
    verifier = HS256Verifier(secret, audience=config.agent()["api"]["audience"])
    app = FastAPI(title="QRIS DevGuard AI", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
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

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    async def ready() -> JSONResponse:
        checks = {"mcp": "up" if await mcp_ping() else "down", "llm": "up" if await llm_ping() else "down"}
        ok = all(v == "up" for v in checks.values())
        return JSONResponse({"status": "ready" if ok else "not_ready", "checks": checks}, status_code=200 if ok else 503)

    @app.post("/api/v1/chat", response_model=ChatResponse)
    async def chat(body: ChatRequest, who: tuple[str, str] = Depends(identity)) -> dict[str, Any]:
        sub, role = who
        started = time.perf_counter()
        out = await agent.run(sub=sub, role=role, message=body.message, context=body.context_dict())
        log.info("route=/api/v1/chat sub=%s role=%s intent=%s risk=%s outcome=%s run_id=%s ms=%d", sub, role,
                 out["intent"], out["risk_level"], out["route"], out["run_id"], (time.perf_counter() - started) * 1000)
        return {k: out[k] for k in ("run_id", "thread_id", "intent", "risk_level", "answer")}

    return app


async def http_ping(url: str) -> bool:
    """Any HTTP answer (even 401) means the service is up; a transport error means down."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            await c.post(url, json={})
        return True
    except httpx.HTTPError:
        return False
