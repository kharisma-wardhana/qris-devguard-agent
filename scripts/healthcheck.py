"""Check every P1 service and print one PASS/FAIL line per check.

Usage: uv run python scripts/healthcheck.py [--env-file .env] [--report PATH] [--timeout S]

Checks: vllm_health, vllm_models, vllm_chat, postgres, qdrant, langfuse_health, langfuse_trace.
Exit code 0 only if every check passes. Output never contains API keys, passwords or hosts:
failures report an HTTP status or an exception class name, nothing more.
Each run appends one JSON line to the report file (git-ignored evals/reports/ by default).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
MODEL_CONFIG = ROOT / "configs" / "model.yaml"
DEFAULT_REPORT = ROOT / "evals" / "reports" / "healthcheck.jsonl"
CHECKS = ("vllm_health", "vllm_models", "vllm_chat", "postgres", "qdrant", "langfuse_health", "langfuse_trace")
PROBE_PROMPT = "Reply with the single word: ready."


@dataclass
class Result:
    check: str
    ok: bool
    ms: float | None = None
    detail: str = ""
    extra: dict = field(default_factory=dict)


def load_env_file(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE reader. Values never leave this process except where a check needs them."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def settings(env_file: Path) -> dict[str, str]:
    merged = load_env_file(env_file)
    merged.update({k: v for k, v in os.environ.items() if v})  # real environment wins
    return merged


def _err(exc: Exception) -> str:
    return type(exc).__name__  # never the message: it can carry a DSN, URL or key


def _timed(fn: Callable[[], Result | None], check: str) -> Result:
    start = time.perf_counter()
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 - every failure becomes a FAIL line
        return Result(check, False, round((time.perf_counter() - start) * 1000, 1), _err(exc))
    if result.ms is None:
        result.ms = round((time.perf_counter() - start) * 1000, 1)
    return result


def _status(check: str, response: httpx.Response, expected=(200,)) -> Result:
    if response.status_code in expected:
        return Result(check, True)
    return Result(check, False, detail=f"HTTP {response.status_code}")


def run_checks(cfg: dict[str, str], client: httpx.Client, pg_connect: Callable, timeout: float) -> list[Result]:
    model = yaml.safe_load(MODEL_CONFIG.read_text(encoding="utf-8"))
    served, probe = model["model"]["served_name"], model["probe"]
    base = cfg.get("VLLM_BASE_URL", "").rstrip("/")
    auth = {"Authorization": f"Bearer {cfg.get('VLLM_API_KEY', '')}"}

    def vllm_health():
        return _status("vllm_health", client.get(base.removesuffix("/v1") + "/health", timeout=timeout))

    def vllm_models():
        r = client.get(f"{base}/models", headers=auth, timeout=timeout)
        if r.status_code != 200:
            return Result("vllm_models", False, detail=f"HTTP {r.status_code}")
        ids = [m.get("id") for m in r.json().get("data", [])]
        return Result("vllm_models", served in ids, detail="" if served in ids else "model not served")

    def vllm_chat():
        body = {
            "model": served,
            "messages": [{"role": "user", "content": PROBE_PROMPT}],
            "temperature": probe["temperature"],
            "top_p": probe["top_p"],
            "max_tokens": probe["max_tokens"],
            "stream": True,
            "chat_template_kwargs": {"enable_thinking": probe["enable_thinking"]},
        }
        start = time.perf_counter()
        ttft = None
        text = []
        with client.stream("POST", f"{base}/chat/completions", headers=auth, json=body, timeout=max(timeout, 60)) as r:
            if r.status_code != 200:
                return Result("vllm_chat", False, detail=f"HTTP {r.status_code}")
            for line in r.iter_lines():
                if not line.startswith("data:") or line.strip() == "data: [DONE]":
                    continue
                delta = json.loads(line[5:])["choices"][0].get("delta", {})
                piece = delta.get("content") or ""
                if piece and ttft is None:
                    ttft = (time.perf_counter() - start) * 1000
                text.append(piece)
        total = (time.perf_counter() - start) * 1000
        ok = ttft is not None and "".join(text).strip() != ""
        return Result("vllm_chat", ok, round(total, 1), "" if ok else "empty response",
                      {"ttft_ms": round(ttft, 1) if ttft else None, "total_ms": round(total, 1)})

    def postgres():
        dsn = cfg.get("DATABASE_URL", "").replace("postgresql+psycopg://", "postgresql://")
        with pg_connect(dsn, connect_timeout=int(timeout)) as conn:
            conn.execute("SELECT 1")
        return Result("postgres", True)

    def qdrant():
        return _status("qdrant", client.get(cfg.get("QDRANT_URL", "").rstrip("/") + "/readyz", timeout=timeout))

    lf = cfg.get("LANGFUSE_HOST", "").rstrip("/")

    def langfuse_health():
        return _status("langfuse_health", client.get(f"{lf}/api/public/health", timeout=timeout))

    def langfuse_trace():
        # Langfuse v4 accepts traces only over OpenTelemetry; one OTLP/HTTP JSON span is enough.
        # The response body is never printed: Langfuse echoes the public key in it.
        now = time.time_ns()
        span = {"traceId": os.urandom(16).hex(), "spanId": os.urandom(8).hex(), "name": "devguard-healthcheck",
                "kind": 1, "startTimeUnixNano": str(now - 1_000_000), "endTimeUnixNano": str(now),
                "attributes": [{"key": "langfuse.trace.name", "value": {"stringValue": "devguard-healthcheck"}},
                               {"key": "langfuse.trace.tags", "value": {"arrayValue": {"values": [{"stringValue": "healthcheck"}]}}}]}
        payload = {"resourceSpans": [{
            "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "devguard-healthcheck"}}]},
            "scopeSpans": [{"scope": {"name": "devguard.healthcheck"}, "spans": [span]}]}]}
        r = client.post(f"{lf}/api/public/otel/v1/traces", json=payload,
                        auth=(cfg.get("LANGFUSE_PUBLIC_KEY", ""), cfg.get("LANGFUSE_SECRET_KEY", "")), timeout=timeout)
        return _status("langfuse_trace", r)

    fns = [vllm_health, vllm_models, vllm_chat, postgres, qdrant, langfuse_health, langfuse_trace]
    return [_timed(fn, name) for fn, name in zip(fns, CHECKS)]


def write_report(path: Path, results: list[Result], served: str) -> None:
    chat = next(r for r in results if r.check == "vllm_chat")
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": served,
        "all_pass": all(r.ok for r in results),
        "ttft_ms": chat.extra.get("ttft_ms"),
        "total_ms": chat.extra.get("total_ms"),
        "checks": [{"check": r.check, "status": "PASS" if r.ok else "FAIL", "ms": r.ms} for r in results],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None,
         pg_connect: Callable | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)

    if pg_connect is None:
        import psycopg
        pg_connect = psycopg.connect

    cfg = settings(args.env_file)
    with httpx.Client(transport=transport) as client:
        results = run_checks(cfg, client, pg_connect, args.timeout)

    width = max(len(c) for c in CHECKS)
    for r in results:
        line = f"{'PASS' if r.ok else 'FAIL'}  {r.check:<{width}}  {r.ms:>8.1f} ms"
        if r.check == "vllm_chat" and r.extra.get("ttft_ms") is not None:
            line += f"  (ttft {r.extra['ttft_ms']} ms)"
        if r.detail:
            line += f"  {r.detail}"
        print(line)

    served = yaml.safe_load(MODEL_CONFIG.read_text(encoding="utf-8"))["model"]["served_name"]
    write_report(args.report, results, served)
    passed = sum(r.ok for r in results)
    print(f"{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
