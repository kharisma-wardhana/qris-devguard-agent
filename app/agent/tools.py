"""The agent's MCP client (p5 spec; AC #6, #7).

- Calls the P4 MCP server over Streamable HTTP with the SDK's ClientSession.
- Never forwards the caller's API token (TRD §13.3): it mints a fresh MCP-audience token for the caller's
  `sub` and `role`, valid for `tools.token_ttl_minutes`, so the server's role matrix decides access.
- A connection failure is retried `tools.transient_retries` times. A tool error envelope (FORBIDDEN,
  INVALID_ARGUMENT, SEARCH_UNAVAILABLE, ...) is an answer, never retried.
- Results carry the tool's JSON or its envelope, plus timing. Arguments are never logged.
"""

from __future__ import annotations

import contextvars
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.agent import config

# p7: the run whose tool calls these are; set by the graph before each call, sent as X-DevGuard-Run-Id
current_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("devguard_agent_run_id",
                                                                           default=None)

log = logging.getLogger("devguard.agent")


@dataclass
class ToolResult:
    tool: str
    ok: bool                         # False: an error envelope, or the server was unreachable
    data: dict[str, Any] = field(default_factory=dict)
    ms: int = 0

    @property
    def error_code(self) -> str | None:
        return None if self.ok else str(self.data.get("code", "TOOL_UNAVAILABLE"))


class ToolClient:
    def __init__(self, url: str, secret: str) -> None:
        cfg = config.agent()["tools"]
        self.url = url
        self._secret = secret
        self.timeout = cfg["timeout_seconds"]
        self.retries = cfg["transient_retries"]
        self.ttl = cfg["token_ttl_minutes"]
        self.tracer: Any = None                    # p8: set by the Agent; spans carry tool, outcome and ms only

    def __repr__(self) -> str:
        return f"ToolClient({self.url!r}, <secret hidden>)"

    def mint(self, sub: str, role: str) -> str:
        from app.mcp_server.auth import mint
        return mint(sub, role, secret=self._secret, ttl_minutes=self.ttl)       # MCP audience (default)

    async def _session_calls(self, token: str, calls: list[tuple[str, dict[str, Any]]]) -> list[ToolResult]:
        headers = {"Authorization": f"Bearer {token}"}
        run_id = current_run_id.get()
        if run_id:
            headers["X-DevGuard-Run-Id"] = run_id               # p7: joins audit rows to the run
        import httpx2
        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        out: list[ToolResult] = []
        async with httpx2.AsyncClient(headers=headers, timeout=self.timeout, follow_redirects=False) as hc:
            async with streamable_http_client(self.url, http_client=hc) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    for name, args in calls:
                        with self._span(name) as span:
                            started = time.perf_counter()
                            res = await session.call_tool(name, args)
                            try:
                                data = json.loads(res.content[0].text)
                            except (ValueError, IndexError, AttributeError):
                                data = {"code": "TOOL_BAD_RESPONSE"}
                            r = ToolResult(name, not res.is_error, data, int((time.perf_counter() - started) * 1000))
                            span.set(outcome=r.error_code or "OK", ms=r.ms)
                        out.append(r)
        return out

    def _span(self, name: str) -> Any:
        from app.observability.tracing import NOOP
        return (self.tracer or NOOP).span(f"mcp.{name}", tool=name)

    async def call(self, sub: str, role: str, calls: list[tuple[str, dict[str, Any]]]) -> list[ToolResult]:
        """Run `calls` in one MCP session. Unreachable server -> TOOL_UNAVAILABLE results, never an exception."""
        if not calls:
            return []
        token = self.mint(sub, role)
        attempts = 1 + self.retries
        for attempt in range(attempts):
            try:
                return await self._session_calls(token, calls)
            except Exception as exc:  # noqa: BLE001 - transport errors arrive wrapped in ExceptionGroups
                log.warning("mcp session failed attempt=%d/%d type=%s", attempt + 1, attempts, type(exc).__name__)
        for name, _ in calls:
            with self._span(name) as span:
                span.set(outcome="TOOL_UNAVAILABLE", ms=0)
        return [ToolResult(name, False, {"code": "TOOL_UNAVAILABLE"}) for name, _ in calls]
