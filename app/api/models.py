"""Strict request/response models for /api/v1/chat (p5 spec; AC #1, #2).

Context blocks may be partial (the agent answers with a clarification listing what is missing), but every
field is typed, bounded and pattern-checked, and unknown fields are refused.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agent import config

_STRICT = ConfigDict(extra="forbid", strict=True)
VERSION = r"^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$"
CASE_ID = r"^[a-z0-9][a-z0-9-]{0,63}$"


class SignatureContext(BaseModel):
    model_config = _STRICT
    algorithm: Literal["SHA256withRSA", "HMAC_SHA512"] | None = None
    fixture_id: str | None = Field(None, pattern=CASE_ID)
    canonicalization_profile: Literal["snap-asymmetric-access-token-v1", "synthetic-snap-symmetric-v1"] | None = None
    test_case_ref: str | None = Field(None, pattern=CASE_ID)


class ChatContext(BaseModel):
    model_config = _STRICT
    api_family: Literal["snap", "qris-mpm"] | None = None
    spec_version: str | None = Field(None, pattern=VERSION)
    endpoint_id: Literal["access-token-b2b", "qr-mpm-generate", "qr-mpm-query"] | None = None
    payload: dict[str, Any] | None = None
    signature: SignatureContext | None = None
    response_code: str | None = Field(None, pattern=r"^[0-9]{7}$")

    @field_validator("payload")
    @classmethod
    def _size(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        limit = config.security()["limits"]["max_payload_bytes"]
        if v is not None and len(json.dumps(v, ensure_ascii=False).encode("utf-8")) > limit:
            raise ValueError("payload too large")
        return v


class ChatRequest(BaseModel):
    model_config = _STRICT
    message: str = Field(min_length=1, max_length=config.agent()["api"]["max_message_chars"])
    context: ChatContext | None = None

    def context_dict(self) -> dict[str, Any]:
        return self.context.model_dump(exclude_none=True) if self.context else {}


class ChatResponse(BaseModel):
    run_id: str
    thread_id: str
    intent: str
    risk_level: str
    answer: dict[str, Any]


class MessageResponse(BaseModel):
    """POST /api/v1/threads/{id}/messages (p6)."""
    thread_id: str
    run_id: str
    status: Literal["completed", "awaiting_approval"]
    intent: str
    risk_level: str
    answer: dict[str, Any]
    approval: dict[str, Any] | None = None


class DecisionRequest(BaseModel):
    """POST /api/v1/approvals/{id}/decision (p6)."""
    model_config = _STRICT
    decision: Literal["approve", "reject"]
    reason: str | None = Field(None, max_length=500)
