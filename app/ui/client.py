"""HTTP client for the Streamlit demo UI (docs/p9-demo-ui-alerts/spec.md Behaviour §1-§6; AC #14, #17).

No Streamlit import here, so it can be tested on its own. The UI talks to /api/v1 only; every request passes the
API's auth, rate limits, redaction and approval checks.

Demo identities get short-lived API-audience tokens minted in-process from the same signing secret as
scripts/mint_dev_token.py (demo-only: whoever runs the UI can act as any configured demo identity). The token lives
in this object only. It is never returned, logged or put in a URL, and `repr()` doesn't show it.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.agent import config
from app.mcp_server import auth

REMINT_MARGIN_SECONDS = 60


@dataclass(frozen=True)
class Identity:
    sub: str
    role: str
    label: str


def identities() -> list[Identity]:
    return [Identity(i["sub"], i["role"], i["label"]) for i in config.agent()["ui"]["identities"]]


def is_approver(identity: Identity) -> bool:
    return identity.role in config.security()["approver_roles"]


class ApiError(Exception):
    """An API failure as the UI shows it: envelope code, message, correlation ID and field names/reasons only."""

    def __init__(self, status: int, code: str, message: str, correlation_id: str | None = None,
                 field_errors: list[dict[str, str]] | None = None, retry_after: str | None = None):
        super().__init__(f"{code}: {message}")
        self.status, self.code, self.message = status, code, message
        self.correlation_id, self.field_errors, self.retry_after = correlation_id, field_errors or [], retry_after


def _error(resp: httpx.Response) -> ApiError:
    try:
        body = resp.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or "code" not in body:
        return ApiError(resp.status_code, f"HTTP_{resp.status_code}", "the API returned an unexpected response")
    fields = [{"field": str(f.get("field", ""))[:120], "reason": str(f.get("reason", ""))[:120]}
              for f in body.get("field_errors") or [] if isinstance(f, dict)]
    return ApiError(resp.status_code, str(body["code"])[:64], str(body.get("message", ""))[:300],
                    str(body.get("correlation_id", ""))[:64] or None, fields, resp.headers.get("Retry-After"))


def parse_context(text: str) -> dict[str, Any] | None:
    """The optional context box: empty -> None; otherwise a JSON object (the API validates its contents)."""
    if not text.strip():
        return None
    try:
        value = json.loads(text)
    except ValueError:
        raise ValueError("context must be a JSON object") from None
    if not isinstance(value, dict):
        raise ValueError("context must be a JSON object")
    return value


class Client:
    def __init__(self, identity: Identity, *, secret: str, api_url: str | None = None,
                 ttl_minutes: int | None = None, timeout: float | None = None,
                 transport: httpx.BaseTransport | None = None, clock: Callable[[], float] = time.time):
        ui = config.agent()["ui"]
        self.identity = identity
        self.api_url = (api_url or ui["api_url"]).rstrip("/")
        self._secret = secret
        self._ttl = ttl_minutes or ui["token_ttl_minutes"]
        self._clock = clock
        self._token: str | None = None
        self._expires = 0.0
        self._http = httpx.Client(base_url=self.api_url, timeout=timeout or ui["request_timeout_seconds"],
                                  transport=transport, follow_redirects=False, trust_env=False)

    def __repr__(self) -> str:
        return f"Client(sub={self.identity.sub!r}, role={self.identity.role!r}, api_url={self.api_url!r})"

    def close(self) -> None:
        self._http.close()

    def _bearer(self) -> str:
        now = self._clock()
        if self._token is None or now >= self._expires - REMINT_MARGIN_SECONDS:
            self._token = auth.mint(self.identity.sub, self.identity.role, secret=self._secret,
                                    ttl_minutes=self._ttl, now=int(now),
                                    audience=config.agent()["api"]["audience"])
            self._expires = int(now) + self._ttl * 60
        return self._token

    def _call(self, method: str, path: str, *, json_body: Any = None, params: dict[str, str] | None = None,
              auth_needed: bool = True) -> Any:
        headers = {"Authorization": f"Bearer {self._bearer()}"} if auth_needed else {}
        try:
            resp = self._http.request(method, path, json=json_body, params=params, headers=headers)
        except httpx.TimeoutException:
            raise ApiError(0, "API_TIMEOUT", "the API did not answer in time") from None
        except httpx.HTTPError:
            raise ApiError(0, "API_UNREACHABLE", f"cannot reach the API at {self.api_url}") from None
        if resp.status_code == 204:
            return None
        if not resp.is_success and not (path == "/health/ready" and resp.status_code == 503):
            raise _error(resp)
        try:
            return resp.json()
        except ValueError:
            raise ApiError(resp.status_code, f"HTTP_{resp.status_code}", "the API returned an unexpected response") from None

    # --- routes ---------------------------------------------------------------------------------------------

    def ready(self) -> dict[str, Any]:
        return self._call("GET", "/health/ready", auth_needed=False)

    def list_threads(self) -> list[dict[str, Any]]:
        return self._call("GET", "/api/v1/threads")["threads"]

    def create_thread(self) -> str:
        return self._call("POST", "/api/v1/threads")["thread_id"]

    def get_thread(self, thread_id: str) -> dict[str, Any]:
        return self._call("GET", f"/api/v1/threads/{thread_id}")

    def history(self, thread_id: str) -> list[dict[str, Any]]:
        return self._call("GET", f"/api/v1/threads/{thread_id}/messages")["runs"]

    def send(self, thread_id: str, message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"message": message}
        if context:
            body["context"] = context
        return self._call("POST", f"/api/v1/threads/{thread_id}/messages", json_body=body)

    def resume(self, thread_id: str) -> dict[str, Any]:
        return self._call("POST", f"/api/v1/threads/{thread_id}/resume")

    def pending_approvals(self) -> list[dict[str, Any]]:
        return self._call("GET", "/api/v1/approvals", params={"status": "pending"})["approvals"]

    def get_approval(self, approval_id: str) -> dict[str, Any]:
        return self._call("GET", f"/api/v1/approvals/{approval_id}")

    def decide(self, approval_id: str, decision: str, reason: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"decision": decision}
        if reason:
            body["reason"] = reason
        return self._call("POST", f"/api/v1/approvals/{approval_id}/decision", json_body=body)

    def alerts(self) -> dict[str, Any]:
        return self._call("GET", "/api/v1/alerts")
