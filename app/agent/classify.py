"""Deterministic intent and risk classification (p5 spec; AC #4, #5).

No model takes part: classification decides which tools run, so it must be testable and immune to prompt
injection. Keyword lists and regexes live in configs/agent.yaml.

Intent, first match wins:
  1. a structured context block: signature -> signature, payload -> payload (timestamp if the message is about
     timestamps), response_code -> response_code
  2. a 7-digit response code in the message -> response_code
  3. keywords: signature, timestamp, response_code, payload, version_question
  4. general_troubleshooting
Risk:
  prohibited  an explicit production phrase, or an action verb followed within `verb_window` words by a
              prohibited object ("process a refund"). A bare mention is a read question.
  high        a write/escalation phrase (also when the message mixes read and write: ambiguous -> high)
  low         everything else
"""

from __future__ import annotations

import re
from typing import Any

from app.agent import config


def _has(text: str, phrase: str) -> bool:
    return re.search(r"(?<![\w-])" + re.escape(phrase.lower()), text) is not None


def _any(text: str, phrases: list[str]) -> bool:
    return any(_has(text, p) for p in phrases)


def extract_code(message: str) -> str | None:
    m = re.search(config.agent()["classify"]["code_regex"], message)
    return m.group(1) if m else None


def extract_version(message: str) -> str | None:
    m = re.search(config.agent()["classify"]["version_regex"], message)
    return m.group(1) if m else None


def classify_intent(message: str, context: dict[str, Any]) -> str:
    kw = config.agent()["classify"]["keywords"]
    text = message.lower()
    if context.get("signature"):
        return "signature"
    if context.get("payload"):
        return "timestamp" if _any(text, kw["timestamp"]) else "payload"
    if context.get("response_code") or extract_code(message):
        return "response_code"
    for intent in ("signature", "timestamp", "response_code", "payload", "version_question"):
        if _any(text, kw[intent]):
            return intent
    return "general_troubleshooting"


def classify_risk(message: str) -> str:
    c = config.agent()["classify"]
    text = message.lower()
    if _any(text, c["prohibited_phrases"]):
        return "prohibited"
    words = re.findall(r"[\w-]+", text)
    verbs = {v.lower() for v in c["action_verbs"]}
    objects = config.prohibited_objects()
    for i, w in enumerate(words):
        if w in verbs:
            window = " ".join(words[i + 1: i + 1 + c["verb_window"]])
            if _any(window, objects):
                return "prohibited"
    if _any(text, c["write"]):
        return "high"
    return "low"
