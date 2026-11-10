"""sanitize_input: redact secrets before anything enters graph state or reaches the model (TRD §19.2; p5 AC #3).

Field names and patterns come from configs/security.yaml (`redact_fields`, `redact_patterns`).
- In structured context, a value under a redact field becomes `[REDACTED]`. An `Authorization` value keeps
  its scheme (`Bearer [REDACTED]`) so the payload validator can still check presence and shape.
- In free text, PEM private-key blocks, bearer tokens and `field: value` / `field=value` pairs for redact
  fields are replaced.
`detected` lists what kinds were found (never the values), so the answer can warn the user.
"""

from __future__ import annotations

import re
from typing import Any

from app.agent import config

REDACTED = "[REDACTED]"


def _fields() -> set[str]:
    return {f.lower() for f in config.security()["redact_fields"]}


def _pem() -> re.Pattern[str]:
    start = config.security()["redact_patterns"]["pem_block"]
    return re.compile(start + r"[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)")


def _bearer() -> re.Pattern[str]:
    return re.compile(config.security()["redact_patterns"]["bearer_token"])


def _pairs() -> re.Pattern[str]:
    names = "|".join(sorted((re.escape(f) for f in _fields()), key=len, reverse=True))
    return re.compile(r"(?i)([\"']?(?:" + names + r")[\"']?\s*[:=]\s*)([\"']?)([^\s\"',}\]]+)")


def redact_text(text: str) -> tuple[str, set[str]]:
    found: set[str] = set()
    out, n = _pem().subn("[REDACTED PRIVATE KEY]", text)
    if n:
        found.add("private_key")
    out, n = _bearer().subn("Bearer " + REDACTED, out)
    if n:
        found.add("bearer_token")

    def pair(m: re.Match[str]) -> str:
        if m.group(3) in (REDACTED, "[REDACTED", "Bearer"):
            return m.group(0)
        found.add("secret_field")
        return f"{m.group(1)}{m.group(2)}{REDACTED}"

    out = _pairs().sub(pair, out)
    return out, found


def _redact_value(key: str, value: Any, found: set[str]) -> Any:
    if key.lower() in _fields() and value not in (None, ""):
        found.add("secret_field")
        if key.lower() == "authorization" and isinstance(value, str):
            scheme = value.split(" ", 1)[0] if " " in value else "Bearer"
            return f"{scheme} {REDACTED}"
        return REDACTED
    return redact_obj(value, found)


def redact_obj(obj: Any, found: set[str]) -> Any:
    """A redacted deep copy. Dict keys are matched case-insensitively against redact_fields."""
    if isinstance(obj, dict):
        return {k: _redact_value(str(k), v, found) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v, found) for v in obj]
    if isinstance(obj, str):
        text, kinds = redact_text(obj)
        found |= kinds
        return text
    return obj


def sanitize(message: str, context: dict[str, Any] | None) -> tuple[str, dict[str, Any], list[str]]:
    found: set[str] = set()
    msg, kinds = redact_text(message)
    found |= kinds
    # Top-level keys are the API's context block names (one is literally `signature`, a redact field):
    # redaction applies inside each block, never to the block name itself.
    ctx = {k: redact_obj(v, found) for k, v in (context or {}).items()}
    return msg, ctx, sorted(found)
