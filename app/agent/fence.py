"""Deterministic answer-echo fencing (p8 spec Behaviour §4; AC #21, #22).

P7 measured that a prompt rule alone does not stop the live model repeating instruction-shaped text from
untrusted input (5 of 10 cases). So fencing is code, in two places, from one marker list (`injection_markers`
in configs/security.yaml: generic categories, not the P7 canaries):

1. Before compose, a sentence of an evidence excerpt (or a verified pattern) that matches a marker is replaced
   by `[instruction-like text removed]`, in the prompt only. The run's `sources` keep the original quote, so
   citations still resolve to the stored chunk.
2. After compose, a sentence of a generated text field (`summary`, `findings[].text`, `next_steps[].text`,
   `limitations`) that matches a marker is removed; a finding or next step left empty is dropped, and one
   limitation line says so.

`answer_echo` is whether the composed answer matched before step 2, so one run shows both whether the model
echoed and whether the user saw it. Matching never returns or logs the matched text: only category names.
"""

from __future__ import annotations

import copy
import re
from functools import lru_cache
from typing import Any

from app.agent import config

REMOVED = "[instruction-like text removed]"
FENCED_NOTE = ("Text resembling an instruction from untrusted input was removed from this answer. The sources "
               "show the original documentation text.")
EMPTY_SUMMARY = "The generated summary was removed because it resembled an instruction; see the findings."
# Sentence boundaries, kept so text re-joins unchanged: end punctuation or a blank line. A single newline is a
# hard wrap inside a sentence (corpus excerpts are wrapped), so it never splits one.
_SPLIT = re.compile(r"((?<=[.!?])\s+|\n\s*\n)")


@lru_cache(maxsize=1)
def _markers() -> tuple[tuple[str, re.Pattern[str]], ...]:
    return tuple((name, re.compile(rx, re.IGNORECASE))
                 for name, rx in config.security()["injection_markers"].items())


def categories(text: str) -> list[str]:
    """The marker categories `text` matches (names only), in config order."""
    return [name for name, rx in _markers() if rx.search(text or "")]


def _sentences(text: str) -> list[str]:
    return _SPLIT.split(text)


def fence_text(text: str, replacement: str = "") -> tuple[str, int]:
    """(text with every marker sentence replaced, number replaced). Separators are kept."""
    parts = _sentences(text or "")
    n = 0
    for i in range(0, len(parts), 2):                   # even indices are sentences, odd ones separators
        if parts[i].strip() and categories(parts[i]):
            parts[i] = replacement
            n += 1
    if not n:
        return text, 0
    out = "".join(parts)
    if not replacement:
        out = re.sub(r"[ \t]{2,}", " ", out).strip()
    return out, n


def fence_evidence(chunks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Copies of the excerpts for the prompt, marker sentences replaced. The originals are not touched."""
    out, total = [], 0
    for c in chunks:
        text, n = fence_text(c.get("quote_or_excerpt", ""), REMOVED)
        total += n
        out.append({**c, "quote_or_excerpt": text} if n else c)
    return out, total


def fence_patterns(patterns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Verified patterns are untrusted too: the same marker replacement as evidence, in the prompt."""
    return [{**p, "title": fence_text(str(p.get("title", "")), REMOVED)[0],
             "content_sanitized": fence_text(str(p.get("content_sanitized", "")), REMOVED)[0]} for p in patterns]


def answer_texts(composed: dict[str, Any]) -> list[str]:
    return ([composed.get("summary") or ""] + [f.get("text") or "" for f in composed.get("findings") or []]
            + [s.get("text") or "" for s in composed.get("next_steps") or []]
            + [x for x in composed.get("limitations") or [] if isinstance(x, str)])


def echoes(composed: dict[str, Any]) -> bool:
    """answer_echo: the composed answer contains instruction-shaped text (measured before fencing)."""
    return any(categories(t) for t in answer_texts(composed))


def fence_answer(composed: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """(fenced copy, sentences removed). Citations and validator refs of kept items are unchanged."""
    out = copy.deepcopy(composed)
    removed = 0
    out["summary"], n = fence_text(out.get("summary") or "")
    removed += n
    if n and not out["summary"].strip():
        out["summary"] = EMPTY_SUMMARY
    for kind in ("findings", "next_steps"):
        kept = []
        for item in out.get(kind) or []:
            item["text"], n = fence_text(item.get("text") or "")
            removed += n
            if item["text"].strip():
                kept.append(item)
        out[kind] = kept
    lims = []
    for x in out.get("limitations") or []:
        text, n = fence_text(x) if isinstance(x, str) else (x, 0)
        removed += n
        if not isinstance(text, str) or text.strip():
            lims.append(text)
    if removed:
        lims.append(FENCED_NOTE)
    out["limitations"] = lims
    return out, removed
