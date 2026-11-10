"""Safe, structured tool errors (TRD §18; p4 spec AC #4, #5).

The envelope is `code`, `message`, `correlation_id` and optional `field_errors`. Messages are fixed
strings per code; a field error names the field and a reason code, never the submitted value.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError

MESSAGES = {
    "UNAUTHENTICATED": "authentication required",
    "FORBIDDEN": "your role may not call this tool",
    "INVALID_ARGUMENT": "one or more arguments are invalid",
    "SEARCH_UNAVAILABLE": "the knowledge base search is not available right now",
    "INTERNAL_ERROR": "the tool failed; quote the correlation_id when reporting it",
}


class ToolFailure(Exception):
    def __init__(self, code: str, field_errors: list[dict[str, str]] | None = None):
        super().__init__(code)
        self.code = code
        self.field_errors = field_errors or []


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def envelope(code: str, correlation_id: str, field_errors: list[dict[str, str]] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"code": code, "message": MESSAGES[code], "correlation_id": correlation_id}
    if field_errors:
        out["field_errors"] = field_errors
    return out


def field_errors_from(exc: ValidationError) -> list[dict[str, str]]:
    """Field path and pydantic error type only. `input`, `ctx` and `msg` can carry the value; they are dropped."""
    out = []
    for err in exc.errors(include_input=False, include_context=False, include_url=False):
        field = ".".join(str(p) for p in err["loc"]) or "<arguments>"
        out.append({"field": field[:120], "reason": str(err["type"])})
    return sorted(out, key=lambda e: (e["field"], e["reason"]))
