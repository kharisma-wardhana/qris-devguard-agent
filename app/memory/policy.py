"""Long-term memory policy (TRD §16; docs/p8-memory-observability/spec.md Behaviour §1).

Pure functions, no database. Everything comes from configs (agent.yaml `memory`, security.yaml `redact_*` and
`pattern_refusal`). The model never decides a save: a preference is saved only from an allowed key and value,
through the API or an explicit configured chat phrase; a pattern only through the propose/review routes.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from app.agent import config
from app.agent.sanitize import redact_text

# memory_decision reason codes (spec Behaviour §1)
SAVED, UPDATED, UNCHANGED = "saved_preference", "updated_preference", "unchanged_preference"
NOT_DEFAULT, NOT_SECRET = "not_saved:default", "not_saved:secret_detected"
NOT_UNRECOGNISED, NOT_ROUTE = "not_saved:unrecognised_preference", "not_saved:route"
NOT_STORE = "not_saved:store_unavailable"          # p8 as built: a matched phrase but no memory store / DB error
REASONS = (SAVED, UPDATED, UNCHANGED, NOT_DEFAULT, NOT_SECRET, NOT_UNRECOGNISED, NOT_ROUTE, NOT_STORE)


def cfg() -> dict[str, Any]:
    return config.agent()["memory"]


def allowed() -> dict[str, list[str]]:
    return {k: list(v) for k, v in cfg()["preferences"].items()}


def is_allowed(key: str, value: str) -> bool:
    return value in allowed().get(key, [])


def note_lines(prefs: dict[str, str]) -> list[str]:
    """The trusted <notes> lines for a user's active preferences, in a fixed key order."""
    notes = cfg()["preference_notes"]
    return [notes[k][prefs[k]] for k in sorted(prefs) if k in notes and prefs[k] in notes[k]]


@lru_cache(maxsize=1)
def _phrases() -> tuple[tuple[re.Pattern[str], str, str], ...]:
    return tuple((re.compile(p["pattern"], re.IGNORECASE), p["key"], p["value"]) for p in cfg()["phrases"])


@lru_cache(maxsize=1)
def _prefix() -> re.Pattern[str]:
    return re.compile(cfg()["phrase_prefix"], re.IGNORECASE)


def match_phrase(message: str) -> tuple[str, str] | None | str:
    """(key, value) for a configured phrase; "unrecognised" for a remember-prefix with no match; None otherwise."""
    for pattern, key, value in _phrases():
        if pattern.search(message) and is_allowed(key, value):
            return key, value
    return "unrecognised" if _prefix().search(message) else None


def decide(message: str, *, route: str, secret_detected: bool) -> tuple[str, tuple[str, str] | None]:
    """What memory_decision may do for a run: (reason, (key, value) to save or None).

    The store turns a save into saved/updated/unchanged. Only a completed read run can save; a run whose input
    held a secret saves nothing at all, even when a phrase matched."""
    if route != "read":
        return NOT_ROUTE, None
    hit = match_phrase(message)
    if hit is None:
        return NOT_DEFAULT, None
    if secret_detected:
        return NOT_SECRET, None
    if hit == "unrecognised":
        return NOT_UNRECOGNISED, None
    return SAVED, hit                                   # provisional: the store decides saved/updated/unchanged


def saveable_hint() -> str:
    """The limitation line for an unrecognised remember-phrase: what can be saved, never what was typed."""
    opts = "; ".join(f"{k}: {', '.join(v)}" for k, v in sorted(allowed().items()))
    return f"Nothing was saved. Preferences that can be saved: {opts}."


@lru_cache(maxsize=1)
def _refusals() -> tuple[tuple[str, re.Pattern[str]], ...]:
    return tuple((name, re.compile(p)) for name, p in config.security()["pattern_refusal"].items())


def refusal_kinds(text: str) -> list[str]:
    """Why a pattern's text must be refused (kinds only, never values): secret-shaped per redact_fields and
    redact_patterns, or personal-data-shaped per pattern_refusal. Empty = acceptable."""
    _, found = redact_text(text)
    kinds = set(found)
    kinds |= {name for name, rx in _refusals() if rx.search(text)}
    return sorted(kinds)
