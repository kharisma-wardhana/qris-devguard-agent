"""LLM clients for compose_diagnosis (p5 spec; AC #9).

`VllmLLM` calls vLLM's OpenAI-compatible endpoint with temperature 0, thinking disabled and JSON-schema
structured output. `FakeLLM` is deterministic for CI; it records every input and can be scripted to return
any output, including broken ones, for policy tests. `compose()` validates the output against OUTPUT_SCHEMA,
retries `json_retries` times, and returns data=None when every attempt fails (the graph then uses the
deterministic fallback answer).
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator

from app.agent import config
from app.agent.prompts import OUTPUT_SCHEMA

_VALIDATOR = Draft202012Validator(OUTPUT_SCHEMA)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


@dataclass
class LLMResult:
    data: dict[str, Any] | None
    attempts: int
    usage: dict[str, int] = field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0})
    ms: int = 0
    error: str | None = None          # last failure kind: bad_json | schema | transport


def parse(text: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        obj = json.loads(_FENCE.sub("", text.strip()))
    except (TypeError, ValueError):
        return None, "bad_json"
    if not isinstance(obj, dict) or any(True for _ in _VALIDATOR.iter_errors(obj)):
        return None, "schema"
    return obj, None


class FakeLLM:
    """Deterministic stand-in. `script(payload) -> dict | str` overrides the default composition."""

    def __init__(self, script: Callable[[dict[str, Any]], Any] | None = None) -> None:
        self.script = script
        self.calls: list[dict[str, str]] = []

    async def complete(self, system: str, user: str, payload: dict[str, Any]) -> tuple[str, dict[str, int]]:
        self.calls.append({"system": system, "user": user})
        out = self.script(payload) if self.script else self.default(payload)
        return (out if isinstance(out, str) else json.dumps(out)), {"prompt_tokens": len(user) // 4,
                                                                     "completion_tokens": 50}

    @staticmethod
    def default(payload: dict[str, Any]) -> dict[str, Any]:
        cites = [c["citation_id"] for c in payload.get("evidence", [])][:2]
        findings = []
        for i, r in enumerate(payload.get("validator_results", [])):
            if r.get("ok"):
                findings.append({"text": f"The {r['tool']} result is described in validator result {i}.",
                                 "status": "confirmed", "citation_ids": cites[:1], "validator_refs": [i]})
        if cites:
            findings.append({"text": "The cited documentation describes the relevant rule.", "status": "likely",
                             "citation_ids": cites, "validator_refs": []})
        return {"summary": "Diagnosis based on the validator results and retrieved documentation.",
                "findings": findings,
                "next_steps": [{"text": "Compare your request with the cited section.", "citation_ids": cites[:1]}],
                "limitations": []}


class VllmLLM:
    def __init__(self) -> None:
        from openai import AsyncOpenAI

        cfg = config.agent()["llm"]
        self.cfg = cfg
        self.model = config.served_model_name()
        self._client = AsyncOpenAI(base_url=os.environ.get(cfg["base_url_env"], "http://127.0.0.1:8080/v1"),
                                   api_key=os.environ.get(cfg["api_key_env"], "missing"),
                                   timeout=cfg["timeout_seconds"], max_retries=0)

    def __repr__(self) -> str:
        return f"VllmLLM(model={self.model!r})"

    def request(self, system: str, user: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": self.cfg["temperature"],
            "max_tokens": self.cfg["max_tokens"],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "diagnosis", "schema": OUTPUT_SCHEMA, "strict": True}},
            "extra_body": {"chat_template_kwargs": {"enable_thinking": self.cfg["enable_thinking"]}},
        }

    async def complete(self, system: str, user: str, payload: dict[str, Any]) -> tuple[str, dict[str, int]]:
        resp = await self._client.chat.completions.create(**self.request(system, user))
        usage = {"prompt_tokens": getattr(resp.usage, "prompt_tokens", 0) or 0,
                 "completion_tokens": getattr(resp.usage, "completion_tokens", 0) or 0}
        return resp.choices[0].message.content or "", usage

    async def ping(self) -> bool:
        try:
            await self._client.models.list(timeout=5)
            return True
        except Exception:  # noqa: BLE001
            return False


async def compose(llm: Any, system: str, user: str, payload: dict[str, Any]) -> LLMResult:
    attempts = 1 + config.agent()["llm"]["json_retries"]
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    started, error = time.perf_counter(), None
    for n in range(1, attempts + 1):
        try:
            text, u = await llm.complete(system, user, payload)
        except Exception:  # noqa: BLE001 - transport/timeout: no retry beyond the JSON retry budget
            error = "transport"
            continue
        for k in usage:
            usage[k] += int(u.get(k, 0))
        data, error = parse(text)
        if data is not None:
            return LLMResult(data, n, usage, int((time.perf_counter() - started) * 1000))
    return LLMResult(None, attempts, usage, int((time.perf_counter() - started) * 1000), error)
