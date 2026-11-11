"""Tracing core (p8 spec AC #14-#17): allowlist, fail-closed mask hook, seeded trace ID, no-op, egress refusal.

The real Langfuse SDK runs with an in-memory span exporter, so the mask hook and the batch drop are exercised
on the SDK's own export path. Nothing leaves the process. Secret-shaped strings are built from parts.
"""

from __future__ import annotations

import logging
import secrets

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.observability import tracing

PEM = "-----" + "BEGIN RSA " + "PRIVATE KEY-----\nMIIBOgIBAAJBAKj34\n-----" + "END RSA " + "PRIVATE KEY-----"
BEARER = "Bear" + "er " + "eyJhbGciOiJIUzI1NiJ9" + "abcdefgh"
SECRET = "s3cr3t" + "Value" + "9876"


def env(**over):
    base = {"LANGFUSE_HOST": "http://localhost:3000", "LANGFUSE_PUBLIC_KEY": "pk-lf-" + secrets.token_hex(8),
            "LANGFUSE_SECRET_KEY": "sk-lf-" + secrets.token_hex(8)}
    return {**base, **over}


@pytest.fixture
def traced():
    exp, scores = InMemorySpanExporter(), []
    t = tracing.build(env(), span_exporter=exp, score_sink=scores.append)
    assert isinstance(t, tracing.LangfuseTracer)
    yield t, exp, scores
    t.shutdown()


def finished(t, exp):
    t.flush()
    return list(exp.get_finished_spans())


def dump(spans) -> str:
    """Everything exported: names, attributes, events and resources, as one string."""
    return "\n".join(f"{s.name} {dict(s.attributes or {})} {[(e.name, dict(e.attributes or {})) for e in s.events]} "
                     f"{dict(s.resource.attributes)}" for s in spans)


# --- off by default --------------------------------------------------------------------------------------

@pytest.mark.parametrize("over", [{"LANGFUSE_PUBLIC_KEY": ""}, {"LANGFUSE_SECRET_KEY": "replace-me"},
                                  {"LANGFUSE_HOST": ""}])
def test_missing_or_placeholder_keys_give_the_noop_tracer(over):
    t = tracing.build(env(**over))
    assert isinstance(t, tracing.NoopTracer) and not t.enabled
    with t.run("r1", name="agent-run", session_id="th", user_id="u") as h, t.span("node", ms=1):
        h.set(status="ok")
    t.score("r1", "tool_success", 1)


def test_disabled_in_config_gives_the_noop_tracer(monkeypatch):
    monkeypatch.setattr(tracing, "cfg", lambda: {**tracing.config.agent()["observability"], "enabled": False})
    assert isinstance(tracing.build(env()), tracing.NoopTracer)


def test_host_outside_the_egress_allowlist_disables_tracing_without_a_client(monkeypatch, caplog):
    import langfuse

    def boom(*a, **k):
        raise AssertionError("no Langfuse client may be created")
    monkeypatch.setattr(langfuse, "Langfuse", boom)
    with caplog.at_level(logging.WARNING, logger="devguard.tracing"):
        t = tracing.build(env(LANGFUSE_HOST="https://cloud.example.com"))
    assert isinstance(t, tracing.NoopTracer)
    lines = [r for r in caplog.records if r.name == "devguard.tracing"]
    assert len(lines) == 1 and "LANGFUSE_HOST" in lines[0].getMessage()
    assert "cloud.example.com" not in lines[0].getMessage()            # names the setting, never the value


# --- seeded trace, allowlist ----------------------------------------------------------------------------

def test_seeded_trace_id_is_stable_and_used_by_the_run_and_a_remote_span(traced):
    t, exp, _ = traced
    tid = tracing.trace_id_for("run-abc")
    assert tid == tracing.trace_id_for("run-abc") != tracing.trace_id_for("run-abd")
    assert len(tid) == 32 and int(tid, 16) >= 0
    with t.run("run-abc", name="agent-run", session_id="thread-1", user_id="dev-1", intent="signature"):
        with t.span("classify_intent", ms=3):
            pass
    with t.span("search_qris_spec", trace_id=tid, outcome="OK"):            # the MCP server side
        pass
    spans = finished(t, exp)
    assert {format(s.context.trace_id, "032x") for s in spans} == {tid}
    root = next(s for s in spans if s.name == "agent-run")
    assert root.attributes["session.id"] == "thread-1" and root.attributes["user.id"] == "dev-1"
    assert "service:agent" in root.attributes["langfuse.trace.tags"]
    child = next(s for s in spans if s.name == "classify_intent")
    assert child.parent is not None and child.parent.span_id == root.context.span_id


def test_allowlist_drops_unknown_names_and_text_values(traced):
    t, exp, _ = traced
    with t.span("compose_diagnosis", run_id="r1", ms=12, note="free text", intent="has spaces in it") as h:
        h.set(outcome="OK", answer="The signature is invalid because")
    out = dump(finished(t, exp))
    assert "r1" in out and "OK" in out
    assert "free text" not in out and "has spaces" not in out and "signature is invalid" not in out
    assert sorted(t.dropped) == ["answer", "intent", "note"]


def test_generation_carries_model_and_usage(traced):
    t, exp, _ = traced
    with t.run("r2", name="agent-run", session_id="th", user_id="u"):
        with t.generation("compose", model="Qwen/Qwen3-8B-AWQ", prompt_version="compose-v2", attempts=1) as g:
            g.usage(120, 40)
    gen = next(s for s in finished(t, exp) if s.name == "compose")
    assert gen.attributes["langfuse.observation.type"] == "generation"
    assert gen.attributes["langfuse.observation.model.name"] == "Qwen/Qwen3-8B-AWQ"
    assert '"input": 120' in gen.attributes["langfuse.observation.usage_details"]


def test_an_exception_is_recorded_by_class_name_only(traced):
    t, exp, _ = traced
    with pytest.raises(ValueError):
        with t.span("execute_read_tools", ms=1):
            raise ValueError("client_secret=" + SECRET + " in the message")
    spans = finished(t, exp)
    out = dump(spans)
    assert SECRET not in out and "in the message" not in out
    assert all(not s.events for s in spans)
    assert '"ValueError"' in out


# --- the mask hook --------------------------------------------------------------------------------------

def test_mask_hook_redacts_fields_patterns_and_deletes_io(traced):
    """Bypass the allowlist (call the SDK directly) to prove the second layer on its own."""
    t, exp, _ = traced
    with t.client.start_as_current_observation(
            name="leaky", input={"message": "hello " + SECRET}, output="answer text " + SECRET,
            metadata={"client_secret": SECRET, "auth": BEARER, "key": PEM, "note": "signature=" + SECRET},
            status_message="password: " + SECRET):
        pass
    spans = finished(t, exp)
    out = dump(spans)
    for needle in (SECRET, "eyJhbGciOiJIUzI1NiJ9", "MIIBOgIBAAJBAKj34", "hello", "answer text"):
        assert needle not in out
    attrs = spans[0].attributes
    assert not any(k.endswith((".input", ".output", ".status_message")) for k in attrs)
    assert attrs["langfuse.observation.metadata.client_secret"] == '"[REDACTED]"'


def test_mask_hook_leaves_clean_attributes_alone(traced):
    t, exp, _ = traced
    with t.span("policy_check", run_id="r3", outcome="OK", ms=4):
        pass
    attrs = finished(t, exp)[0].attributes
    assert attrs["langfuse.observation.metadata.run_id"] == '"r3"'
    assert attrs["langfuse.observation.metadata.ms"] == "4"


def test_mask_hook_raising_drops_the_whole_batch(traced, monkeypatch):
    t, exp, _ = traced

    def broken(key, value):
        raise RuntimeError("mask failure")
    monkeypatch.setattr(tracing, "_hit", broken)
    with t.span("one", run_id="r4"), t.span("two", run_id="r4"):
        pass
    assert finished(t, exp) == []


# --- scores, delay, corpus version ----------------------------------------------------------------------

def test_score_goes_to_the_seeded_trace_and_a_text_comment_is_dropped(traced):
    t, _, scores = traced
    t.score("r5", "incident_event", 1, comment="APPROVAL_INVALID")
    t.score("r5", "incident_event", 1, comment="approval for client_secret=" + SECRET)
    assert scores[0] == {"name": "incident_event", "value": 1, "trace_id": tracing.trace_id_for("r5"),
                         "data_type": "BOOLEAN", "comment": "APPROVAL_INVALID"}
    assert scores[1]["comment"] is None


def test_a_failing_score_sink_never_raises(caplog):
    def sink(item):
        raise ConnectionError("down")
    t = tracing.build(env(), span_exporter=InMemorySpanExporter(), score_sink=sink)
    t.score("r6", "tool_success", 1)
    t.shutdown()


@pytest.mark.parametrize(("raw", "ms"), [("", 0), ("abc", 0), ("-5", 0), ("1500", 1500), ("999999", 5000)])
def test_test_delay_is_off_by_default_and_capped(raw, ms):
    assert tracing.test_delay_ms({"DEVGUARD_TEST_DELAY_MS": raw}) == ms


def test_corpus_version_is_a_manifest_hash_prefix():
    v = tracing.corpus_version()
    assert len(v) == 12 and int(v, 16) >= 0
