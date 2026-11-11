"""Langfuse tracing core (p8 spec Behaviour §2; AC #14-#17).

- **Off unless configured.** `observability.enabled` false, a missing or placeholder `LANGFUSE_*` key, or a
  host outside `egress_allowlist` gives the `NoopTracer` (the CI default). An egress refusal logs one line naming
  the setting, never its value, and no client or socket is created.
- **One trace per run.** The trace ID is seeded from `run_id` (`Langfuse.create_trace_id(seed=...)`), so the
  agent and the MCP server attach spans to the same trace without passing anything new over the wire.
- **Allowlist first.** Span helpers accept only the attribute names in `ALLOWED_ATTRS`, and only scalar values;
  a string must be a short token (an ID, name or code, no spaces). Text never becomes a span attribute, input
  or output. Rejected names are counted in `dropped` (names only).
- **Mask hook second.** `mask_otel_spans` runs at export over every string attribute: input/output and status
  attributes are deleted, and a value under a `redact_fields` name or matching a `redact_patterns` rule becomes
  `[REDACTED]`. It never catches its own errors: if it raises, the SDK drops the whole batch (fail closed).
- **Exceptions stay out of the trace.** OpenTelemetry records an escaping exception as a span event (message and
  stack trace), which the mask hook cannot change. Span helpers catch it, mark the span ERROR with the exception
  class name only, close the span, then re-raise.
- **Tracing never breaks a request.** The SDK exports on a background thread with a short timeout.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from app.agent import config
from app.agent.sanitize import REDACTED, redact_text

log = logging.getLogger("devguard.tracing")

ROOT = Path(__file__).resolve().parents[2]

# Every attribute name a DevGuard span may carry: IDs, names, codes, counts, statuses, latencies, token counts.
ALLOWED_ATTRS = frozenset({
    "run_id", "thread_id", "correlation_id", "model", "model_revision", "prompt_version", "corpus_version",
    "intent", "risk", "route", "status", "node", "tool", "outcome", "ms", "attempts", "fallback",
    "prompt_tokens", "completion_tokens", "result_status", "count", "hits", "top_k", "rerank", "stage",
    "memory_decision", "patterns_shown", "approval_status", "error_code", "delay_ms", "resumed",
})
_TOKEN = re.compile(r"^[A-Za-z0-9_.:@/+\-]{0,128}$")

# Attributes the mask hook always deletes: DevGuard never sets them, so their presence means something leaked.
_DELETE = ("langfuse.observation.input", "langfuse.observation.output", "langfuse.trace.input",
           "langfuse.trace.output", "langfuse.observation.status_message")
_JSON_PREFIXES = ("langfuse.trace.metadata.", "langfuse.observation.metadata.")
_PLACEHOLDERS = {"", "replace-me"}


def cfg() -> dict[str, Any]:
    return config.agent()["observability"]


def trace_id_for(run_id: str) -> str:
    """The seeded Langfuse trace ID for a run: 32 lowercase hex characters, stable across processes."""
    from langfuse import Langfuse
    return Langfuse.create_trace_id(seed=run_id)


def corpus_version() -> str:
    """SHA-256 prefix of the source manifest (plan.md: computed in the agent's process, not reported by a tool)."""
    try:
        return hashlib.sha256((ROOT / cfg()["manifest_path"]).read_bytes()).hexdigest()[:12]
    except OSError:
        return "unknown"


def test_delay_ms(env: Mapping[str, str] | None = None) -> int:
    """The test-only delay for the p95 alert test: 0 unless set, capped at `test_delay_max_ms`."""
    o = cfg()
    raw = (env if env is not None else os.environ).get(o["test_delay_env"], "")
    try:
        ms = int(raw)
    except ValueError:
        return 0
    return max(0, min(ms, int(o["test_delay_max_ms"])))


def clean_attrs(attrs: Mapping[str, Any], dropped: list[str] | None = None) -> dict[str, Any]:
    """Keep allowlisted names with scalar values; a string must be a short token. Drops are recorded by name."""
    out: dict[str, Any] = {}
    for name, value in attrs.items():
        if value is None:
            continue
        ok = name in ALLOWED_ATTRS and (
            isinstance(value, (bool, int, float)) or (isinstance(value, str) and bool(_TOKEN.match(value))))
        if ok:
            out[name] = value
        elif dropped is not None:
            dropped.append(name)
    return out


def _clean_comment(comment: Optional[str]) -> Optional[str]:
    return comment if comment is not None and _TOKEN.match(comment) else None


# --- the export-stage mask hook ---------------------------------------------------------------------------

def _hit(key: str, value: str) -> bool:
    fields = {f.lower() for f in config.security()["redact_fields"]}
    if key.rsplit(".", 1)[-1].lower() in fields:
        return True
    text = value
    if key.startswith(_JSON_PREFIXES):
        try:
            decoded = json.loads(value)
            text = decoded if isinstance(decoded, str) else value
        except json.JSONDecodeError:
            text = value
    redacted, found = redact_text(text)
    return bool(found) or redacted != text


def mask_otel_spans(*, params: Any) -> Any:
    """Langfuse `mask_otel_spans` hook. Deliberately has no try/except: an error drops the batch (fail closed)."""
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch
    patches = {}
    for ident, span in params.spans.items():
        delete = [k for k in span.attributes if k in _DELETE]
        sets: dict[str, Any] = {}
        for key, value in span.attributes.items():
            if key in delete:
                continue
            if isinstance(value, str) and _hit(key, value):
                sets[key] = json.dumps(REDACTED) if key.startswith(_JSON_PREFIXES) else REDACTED
            elif isinstance(value, (list, tuple)) and any(isinstance(v, str) and _hit(key, v) for v in value):
                sets[key] = [REDACTED]
        if delete or sets:
            patches[ident] = OtelSpanPatch(set_attributes=sets, delete_attributes=tuple(delete))
    return MaskOtelSpansResult(span_patches=patches)


# --- tracers ---------------------------------------------------------------------------------------------

class _NoopHandle:
    def set(self, **attrs: Any) -> None:
        return None

    def usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        return None


class NoopTracer:
    """Tracing off: every call is a cheap no-op, and no client, thread or socket exists."""

    enabled = False

    def __init__(self) -> None:
        self.dropped: list[str] = []

    @contextmanager
    def run(self, run_id: str, **_: Any) -> Iterator[_NoopHandle]:
        yield _NoopHandle()

    @contextmanager
    def span(self, name: str, *, trace_id: str | None = None, **_: Any) -> Iterator[_NoopHandle]:
        yield _NoopHandle()

    @contextmanager
    def generation(self, name: str, *, model: str, **_: Any) -> Iterator[_NoopHandle]:
        yield _NoopHandle()

    def score(self, run_id: str, name: str, value: Any, *, comment: str | None = None,
              data_type: str = "BOOLEAN") -> None:
        return None

    def flush(self) -> None:
        return None

    def shutdown(self) -> None:
        return None


class _Handle:
    def __init__(self, obs: Any, dropped: list[str]) -> None:
        self._obs, self._dropped = obs, dropped

    def set(self, **attrs: Any) -> None:
        clean = clean_attrs(attrs, self._dropped)
        if clean:
            self._obs.update(metadata=clean)

    def usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        self._obs.update(usage_details={"input": int(prompt_tokens), "output": int(completion_tokens)})


class LangfuseTracer:
    enabled = True

    def __init__(self, client: Any, provider: Any, score_sink: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.client, self.provider, self.score_sink = client, provider, score_sink
        self.dropped: list[str] = []

    @contextmanager
    def _observe(self, name: str, as_type: str, attrs: Mapping[str, Any], **kw: Any) -> Iterator[_Handle]:
        err: BaseException | None = None
        with self.client.start_as_current_observation(name=name, as_type=as_type,
                                                      metadata=clean_attrs(attrs, self.dropped), **kw) as obs:
            try:
                yield _Handle(obs, self.dropped)
            except BaseException as e:   # noqa: BLE001 - recorded by class name only, then re-raised below
                err = e
                obs.update(level="ERROR", metadata={"error_code": type(e).__name__})
        if err is not None:
            raise err

    @contextmanager
    def run(self, run_id: str, *, name: str, session_id: str, user_id: str, tags: list[str] | None = None,
            **attrs: Any) -> Iterator[_Handle]:
        """The root span of a run's trace (seeded ID), with session, user, tags and metadata propagated."""
        from langfuse import propagate_attributes
        meta = {k: str(v) for k, v in clean_attrs({"run_id": run_id, **attrs}, self.dropped).items()}
        safe_tags = [t for t in [cfg()["service_tag"], *(tags or [])] if _TOKEN.match(t)]
        with self._observe(name, "span", {"run_id": run_id, **attrs},
                           trace_context={"trace_id": trace_id_for(run_id)}) as h:
            with propagate_attributes(session_id=session_id, user_id=user_id, tags=safe_tags, metadata=meta,
                                      trace_name=name):
                yield h

    @contextmanager
    def span(self, name: str, *, trace_id: str | None = None, **attrs: Any) -> Iterator[_Handle]:
        """A child of the current span; with `trace_id`, a span attached to that trace (the MCP server side)."""
        kw = {"trace_context": {"trace_id": trace_id}} if trace_id else {}
        with self._observe(name, "span", attrs, **kw) as h:
            yield h

    @contextmanager
    def generation(self, name: str, *, model: str, **attrs: Any) -> Iterator[_Handle]:
        with self._observe(name, "generation", attrs, model=model if _TOKEN.match(model) else None) as h:
            yield h

    def score(self, run_id: str, name: str, value: Any, *, comment: str | None = None,
              data_type: str = "BOOLEAN") -> None:
        """A score on the run's trace. The comment must be a token (a code), or it is dropped."""
        item = {"name": name, "value": value, "trace_id": trace_id_for(run_id), "data_type": data_type,
                "comment": _clean_comment(comment)}
        try:
            if self.score_sink is not None:
                self.score_sink(item)
            else:
                self.client.create_score(**item)
        except Exception:  # noqa: BLE001 - a score never fails a request
            log.warning("tracing: score %s not recorded", name)

    def flush(self) -> None:
        try:
            self.client.flush()
        except Exception:  # noqa: BLE001
            log.warning("tracing: flush failed")

    def shutdown(self) -> None:
        try:
            self.client.shutdown()
        except Exception:  # noqa: BLE001
            log.warning("tracing: shutdown failed")


Tracer = NoopTracer | LangfuseTracer


def build(env: Mapping[str, str] | None = None, *, span_exporter: Any = None,
          score_sink: Callable[[dict[str, Any]], None] | None = None) -> Tracer:
    """The process's tracer, from `observability` config and `LANGFUSE_*` (spec Behaviour §2)."""
    from app.security.egress import EgressRefused, check
    o = cfg()
    env = env if env is not None else os.environ
    host, pk, sk = (env.get(o[k], "") for k in ("host_env", "public_key_env", "secret_key_env"))
    if not o.get("enabled") or host in _PLACEHOLDERS or pk in _PLACEHOLDERS or sk in _PLACEHOLDERS:
        return NoopTracer()
    try:
        check(o["host_env"], host)
    except EgressRefused as e:
        log.warning("tracing disabled: %s", e)               # names the setting, never the host
        return NoopTracer()
    from langfuse import Langfuse
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    provider = TracerProvider(resource=Resource.create({"service.name": "qris-devguard",
                                                        "langfuse.environment": o["environment"]}))
    client = Langfuse(public_key=pk, secret_key=sk, host=host, timeout=int(o["export_timeout_seconds"]),
                      environment=o["environment"], mask_otel_spans=mask_otel_spans, tracer_provider=provider,
                      span_exporter=span_exporter, sample_rate=1.0)
    return LangfuseTracer(client, provider, score_sink)
