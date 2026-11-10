"""Strict argument models for the four P4 tools (TRD §13.3; p4 spec AC #4).

Every model forbids extra fields, uses strict types, and bounds every string. Identifiers are
enums or anchored patterns, so no argument can carry a URL, SQL, a shell command or a path.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.mcp_server import config
from app.validators.canonical import PROFILES

VERSION = r"^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$"
CASE_ID = r"^[a-z0-9][a-z0-9-]{0,63}$"
ApiFamily = Literal["snap", "qris-mpm"]
EndpointId = Literal["access-token-b2b", "qr-mpm-generate", "qr-mpm-query"]
Algorithm = Literal["SHA256withRSA", "HMAC_SHA512"]
Profile = Literal["snap-asymmetric-access-token-v1", "synthetic-snap-symmetric-v1"]

_STRICT = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=False)


class SearchArgs(BaseModel):
    model_config = _STRICT
    query: str = Field(min_length=1, max_length=500, description="Question or symptom, in Indonesian or English")
    api_family: ApiFamily | None = Field(None, description="Limit results to one API family's documents")
    version: str | None = Field(None, pattern=VERSION, description="Limit results to one document version")
    top_k: int = Field(5, ge=1, le=10, description="Number of passages to return")


class PayloadArgs(BaseModel):
    model_config = _STRICT
    api_family: ApiFamily
    endpoint_id: EndpointId
    spec_version: str = Field(pattern=VERSION)
    payload: dict[str, Any] = Field(description='Sanitized request: {"headers": {...}, "body": {...}}')

    @field_validator("payload")
    @classmethod
    def _size(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(v, ensure_ascii=False).encode("utf-8")) > config.max_payload_bytes():
            raise ValueError("payload too large")
        return v


class SignatureArgs(BaseModel):
    model_config = _STRICT
    algorithm: Algorithm
    fixture_id: str = Field(pattern=CASE_ID, description="Test-key fixture ID, e.g. syn-hmac-01")
    canonicalization_profile: Profile
    test_payload_ref: str = Field(pattern=CASE_ID, description="Signature test case ID, e.g. sig-gen-valid")


class ResponseCodeArgs(BaseModel):
    model_config = _STRICT
    code: str = Field(pattern=r"^[0-9]{7}$", description="7-digit SNAP response code")
    api_family: ApiFamily
    spec_version: str = Field(pattern=VERSION)


assert set(Profile.__args__) == set(PROFILES)          # the enum and the implemented profiles never drift
