"""Business rules kept apart from JSON Schema so each rule's source and version can be audited (TRD §14.1).

Each rule is a named, versioned function over the header-normalised payload. A rule only looks at
fields that already passed their schema pattern; malformed fields are the schema's error, not the rule's.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3})(Z|[+-]\d{2}:\d{2})$")

Issue = dict[str, Any]


@dataclass(frozen=True)
class Rule:
    rule_id: str
    version: str
    endpoints: frozenset[str]
    source: dict[str, str]
    check: Callable[[dict[str, Any]], list[Issue]]


def parse_timestamp(value: Any) -> datetime | None:
    """A real calendar instant for a pattern-valid timestamp, else None (None also for a bad pattern)."""
    if not isinstance(value, str):
        return None
    m = _TS.match(value)
    if not m:
        return None
    offset = "+00:00" if m.group(2) == "Z" else m.group(2)
    try:
        return datetime.fromisoformat(m.group(1) + offset)
    except ValueError:
        return None


def _pattern_ok(value: Any) -> bool:
    return isinstance(value, str) and bool(_TS.match(value))


def _issue(code: str, pointer: str, message: str) -> Issue:
    return {"code": code, "pointer": pointer, "message": message}


def _calendar(payload: dict[str, Any]) -> list[Issue]:
    out = []
    fields = [("/headers/X-TIMESTAMP", payload.get("headers", {}).get("X-TIMESTAMP")),
              ("/body/validityPeriod", (payload.get("body") or {}).get("validityPeriod")
               if isinstance(payload.get("body"), dict) else None)]
    for pointer, value in fields:
        if _pattern_ok(value) and parse_timestamp(value) is None:
            out.append(_issue("RULE_TIMESTAMP_INVALID_DATE", pointer, "timestamp is not a real calendar date/time"))
    return out


def _validity_after_timestamp(payload: dict[str, Any]) -> list[Issue]:
    body = payload.get("body")
    if not isinstance(body, dict) or "validityPeriod" not in body:
        return []
    ts = parse_timestamp(payload.get("headers", {}).get("X-TIMESTAMP"))
    vp = parse_timestamp(body.get("validityPeriod"))
    if ts is None or vp is None:
        return []
    if vp <= ts:
        return [_issue("RULE_VALIDITY_NOT_AFTER_TIMESTAMP", "/body/validityPeriod",
                       "validityPeriod must be later than X-TIMESTAMP")]
    return []


def _one_original_reference(payload: dict[str, Any]) -> list[Issue]:
    body = payload.get("body")
    if not isinstance(body, dict):
        return []
    present = [k for k in ("originalReferenceNo", "originalPartnerReferenceNo") if k in body]
    if len(present) != 1:
        return [_issue("RULE_ORIGINAL_REFERENCE_COUNT", "/body",
                       "exactly one of originalReferenceNo and originalPartnerReferenceNo is required")]
    return []


def _service_code_47(payload: dict[str, Any]) -> list[Issue]:
    body = payload.get("body")
    if isinstance(body, dict) and isinstance(body.get("serviceCode"), str) and body["serviceCode"] != "47":
        return [_issue("RULE_SERVICE_CODE_MISMATCH", "/body/serviceCode",
                       "serviceCode must be the service that created the QR (47)")]
    return []


_MPM = "synthetic-qris-mpm-service-spec"
RULES: tuple[Rule, ...] = (
    Rule("timestamp-calendar", "0.1.0", frozenset({"access-token-b2b", "qr-mpm-generate", "qr-mpm-query"}),
         {"document_id": "snap-technical-security-standard / " + _MPM, "section": "2.1.5(a) / 1.3"}, _calendar),
    Rule("validity-after-timestamp", "0.1.0", frozenset({"qr-mpm-generate"}),
         {"document_id": _MPM, "section": "2. Generate QR (validityPeriod)"}, _validity_after_timestamp),
    Rule("one-original-reference", "0.1.0", frozenset({"qr-mpm-query"}),
         {"document_id": _MPM, "section": "3. Query Payment (request fields)"}, _one_original_reference),
    Rule("service-code-47", "0.1.0", frozenset({"qr-mpm-query"}),
         {"document_id": _MPM, "section": "3. Query Payment (serviceCode)"}, _service_code_47),
)


def run(endpoint_id: str, payload: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []
    for rule in RULES:
        if endpoint_id in rule.endpoints:
            for issue in rule.check(payload):
                issues.append({**issue, "rule_id": rule.rule_id, "rule_version": rule.version, "source": rule.source})
    return issues
