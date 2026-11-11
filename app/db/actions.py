"""The one side-effecting action: its normalized payload, action hash and idempotency key (TRD §11.4, §15.2).

Shared by the agent (when it asks for approval) and the MCP tool (when it re-checks before writing), so both
sides hash exactly the same bytes.

  normalized   {title, body, evidence_ids (sorted, unique), priority}; strings NFC-normalized and stripped
  action_hash  sha256("create_support_ticket" \\x1f normalized_json)
  idem key     sha256(thread_id \\x1f "create_support_ticket" \\x1f normalized_json)

Any change to any field changes both, which invalidates an approval bound to the old hash.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any

ACTION_TYPE = "create_support_ticket"
SEP = "\x1f"


def _text(s: Any) -> str:
    return unicodedata.normalize("NFC", str(s)).strip()


def normalize(payload: dict[str, Any]) -> dict[str, Any]:
    return {"title": _text(payload["title"]), "body": _text(payload["body"]),
            "evidence_ids": sorted({_text(e) for e in payload.get("evidence_ids", []) if _text(e)}),
            "priority": _text(payload["priority"])}


def canonical(payload: dict[str, Any]) -> str:
    return json.dumps(normalize(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def action_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256((ACTION_TYPE + SEP + canonical(payload)).encode("utf-8")).hexdigest()


def idempotency_key(thread_id: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256((thread_id + SEP + ACTION_TYPE + SEP + canonical(payload)).encode("utf-8")).hexdigest()
