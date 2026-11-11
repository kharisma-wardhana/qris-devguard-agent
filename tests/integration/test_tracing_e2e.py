"""Tracing end to end (p8 spec AC #12, #13, #14, #16).

The agent and the MCP harness each get their own tracer (the real Langfuse SDK) with an in-memory exporter, as
two processes would. Nothing leaves the process except in the blackhole test, which exports to a loopback port
that accepts connections and never answers. Secret-shaped strings are built from parts.
"""

from __future__ import annotations

import asyncio
import secrets
import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.agent import config
from app.agent.graph import Agent
from app.agent.harness import McpHarness
from app.agent.llm import FakeLLM
from app.agent.tools import ToolClient
from app.api.app import create_app
from app.db import store
from app.mcp_server import auth
from app.observability import tracing

LOOPBACK = ".".join(["127", "0", "0", "1"])
SIG_CTX = {"api_family": "snap", "signature": {"algorithm": "SHA256withRSA", "fixture_id": "syn-rsa-01",
                                               "canonicalization_profile": "snap-asymmetric-access-token-v1",
                                               "test_case_ref": "sig-at-wrong-separator"}}
QUESTION = "Why is my access-token signature rejected?"

CLIENT_SECRET = "cs-" + "Zq8" + "Lm2Rt7Wx4Vb9"
BEARER_VALUE = "eyJhbGciOi" + "JSUzI1NiJ9" + "Qx7Kp2"
PEM_BODY = "MIIEow" + "IBAAKCAQEAs9Hk"
SIG_VALUE = "c2lnbmF0" + "dXJlLXRlc3Q9"
PEM = "-----" + "BEGIN RSA " + "PRIVATE KEY-----\n" + PEM_BODY + "\n-----" + "END RSA " + "PRIVATE KEY-----"
LEAKY = (f"My access-token signature keeps failing, please create a ticket for this. client_secret={CLIENT_SECRET} "
         f"Authorization: Bearer {BEARER_VALUE} signature={SIG_VALUE}\n{PEM}")
NEEDLES = (CLIENT_SECRET, BEARER_VALUE, PEM_BODY, SIG_VALUE, "keeps failing", "create a ticket",
           "Diagnosis based on the validator results")          # secrets, the message, the (fake) answer text


def keys():
    return {"LANGFUSE_HOST": "http://localhost:3000", "LANGFUSE_PUBLIC_KEY": "pk-lf-" + secrets.token_hex(8),
            "LANGFUSE_SECRET_KEY": "sk-lf-" + secrets.token_hex(8)}


class Traced:
    def __init__(self):
        self.exporter, self.scores = InMemorySpanExporter(), []
        self.tracer = tracing.build(keys(), span_exporter=self.exporter, score_sink=self.scores.append)
        assert self.tracer.enabled

    def spans(self):
        self.tracer.flush()
        return list(self.exporter.get_finished_spans())


def tid(span) -> str:
    return format(span.context.trace_id, "032x")


def dump(*traced: Traced) -> str:
    parts = []
    for t in traced:
        for s in t.spans():
            parts.append(f"{s.name} {dict(s.attributes or {})} {[(e.name, dict(e.attributes or {})) for e in s.events]}")
        parts.extend(str(x) for x in t.scores)
    return "\n".join(parts)


@pytest.fixture(scope="module")
def pair():
    agent_side, server_side = Traced(), Traced()
    with McpHarness(rate_limits=False, tracer=server_side.tracer) as h:
        yield agent_side, server_side, h
    agent_side.tracer.shutdown()
    server_side.tracer.shutdown()


def test_one_trace_with_node_generation_and_mcp_spans_on_both_sides(pair):
    agent_side, server_side, h = pair
    agent = Agent(ToolClient(h.url, h.secret), FakeLLM(), tracer=agent_side.tracer)
    out = asyncio.run(agent.run(sub="dev-1", role="developer", message=QUESTION, context=SIG_CTX))
    trace_id = tracing.trace_id_for(out["run_id"])
    mine = [s for s in agent_side.spans() if tid(s) == trace_id]
    names = {s.name for s in mine}

    # one span per executed node, the generation, one span per MCP call (AC #12)
    assert set(out["trace"]["nodes"]) <= names
    assert {"agent-run", "llm.compose", "mcp.search_qris_spec", "mcp.validate_test_signature"} <= names
    root = next(s for s in mine if s.name == "agent-run")
    assert root.attributes["session.id"] == out["thread_id"] and root.attributes["user.id"] == "dev-1"
    meta = {k.rsplit(".", 1)[-1]: v for k, v in root.attributes.items() if ".metadata." in k}
    assert meta["run_id"] == f'"{out["run_id"]}"' and meta["prompt_version"] == '"compose-v2"'
    assert meta["model"] == f'"{config.model_identity()[0]}"' and len(meta["corpus_version"]) == 14
    assert meta["route"] == '"read"' and "ms" in meta
    assert "prompt:compose-v2" in root.attributes["langfuse.trace.tags"]
    gen = next(s for s in mine if s.name == "llm.compose")
    assert gen.attributes["langfuse.observation.type"] == "generation"
    assert gen.attributes["langfuse.observation.model.name"] == config.served_model_name()
    assert '"input"' in gen.attributes["langfuse.observation.usage_details"]
    call = next(s for s in mine if s.name == "mcp.validate_test_signature")
    assert call.attributes["langfuse.observation.metadata.outcome"] == '"OK"'
    node = next(s for s in mine if s.name == "execute_read_tools")
    assert call.parent.span_id == node.context.span_id                      # the call is inside its node

    # the server's spans join the same trace (AC #13), retrieval stages under the tool span
    server = [s for s in server_side.spans() if tid(s) == trace_id]
    snames = {s.name for s in server}
    assert {"mcp_server.search_qris_spec", "mcp_server.validate_test_signature", "retrieval.bm25_encode",
            "retrieval.dense_embed", "retrieval.fused_query"} <= snames
    tool = next(s for s in server if s.name == "mcp_server.search_qris_spec")
    assert all(s.parent.span_id == tool.context.span_id for s in server if s.name.startswith("retrieval."))
    val = next(s for s in server if s.name == "mcp_server.validate_test_signature")
    assert val.attributes["langfuse.observation.metadata.result_status"] == '"INVALID"'


def test_spans_never_carry_message_answer_or_secrets(pair):
    agent_side, server_side, h = pair
    agent = Agent(ToolClient(h.url, h.secret), FakeLLM(), tracer=agent_side.tracer)
    asyncio.run(agent.run(sub="dev-1", role="developer", message=LEAKY, context={
        **SIG_CTX, "headers": {"Authorization": "Bearer " + BEARER_VALUE}, "client_secret": CLIENT_SECRET}))
    out = dump(agent_side, server_side)
    for needle in NEEDLES:
        assert needle not in out


# --- full escalation and approval on PostgreSQL (AC #14) --------------------------------------------------

@pytest.mark.db
def test_no_secret_in_any_span_through_escalation_approval_and_resume(migrated_db_url, db_roles):
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    agent_side, server_side = Traced(), Traced()
    with McpHarness(db_conninfo=migrated_db_url, audit_conninfo=db_roles.url("audit"), rate_limits=False,
                    tracer=server_side.tracer) as h:
        async def go(fn):
            async with AsyncPostgresSaver.from_conn_string(migrated_db_url) as saver:
                agent = Agent(ToolClient(h.url, h.secret), FakeLLM(), checkpointer=saver, db_conninfo=migrated_db_url,
                              tracer=agent_side.tracer)
                return await fn(agent)
        first = asyncio.run(go(lambda a: a.run(sub="dev-1", role="developer", message=LEAKY, context=SIG_CTX)))
        assert first["status"] == "awaiting_approval"
        with store.connect(migrated_db_url) as c:
            assert store.decide(c, first["approval"]["approval_id"], "sup-1", "approve")[0] == store.DECIDED
        done = asyncio.run(go(lambda a: a.resume(first["thread_id"])))
        assert done["action_result"]["status"] == "created"
        spans = agent_side.spans()
        resume_tid = tracing.trace_id_for(f"{first['run_id']}:resume")
        resumed = [s for s in spans if tid(s) == resume_tid]
        assert {"agent-resume", "request_human_approval", "execute_approved_action", "approval_wait",
                "mcp.create_support_ticket"} <= {s.name for s in resumed}
        root = next(s for s in resumed if s.name == "agent-resume")
        assert root.attributes["session.id"] == first["thread_id"]          # linked to the first trace
        paused = next(s for s in spans if s.name == "request_human_approval" and tid(s) != resume_tid)
        assert paused.attributes["langfuse.observation.metadata.outcome"] == '"interrupted"'
        assert paused.attributes.get("langfuse.observation.level") != "ERROR"
        out = dump(agent_side, server_side)
    for needle in NEEDLES:
        assert needle not in out
    agent_side.tracer.shutdown()
    server_side.tracer.shutdown()


# --- Langfuse unreachable (AC #16) ------------------------------------------------------------------------

@pytest.fixture
def blackhole():
    """A loopback port that accepts TCP connections and never reads or answers."""
    srv = socket.socket()
    srv.bind((LOOPBACK, 0))
    srv.listen(64)
    yield srv.getsockname()[1]
    srv.close()


def test_chat_returns_200_when_langfuse_never_answers(blackhole):
    env = {**keys(), "LANGFUSE_HOST": f"http://{LOOPBACK}:{blackhole}"}
    tracer = tracing.build(env)                                  # the real OTLP exporter, background thread
    assert tracer.enabled
    with McpHarness(rate_limits=False) as h:
        from app.security.ratelimit import TokenBucket
        lenient = {k: TokenBucket(10**6, 10**6) for k in ("per_address", "api_agent_run", "api_other")}

        async def up() -> bool:
            return True
        app = create_app(agent=Agent(ToolClient(h.url, h.secret), FakeLLM(), tracer=tracer), secret=h.secret,
                         mcp_ping=up, llm_ping=up, limits=lenient)
        tok = auth.mint("dev-1", "developer", secret=h.secret, audience=config.agent()["api"]["audience"])
        client = TestClient(app, raise_server_exceptions=False)
        timings = []
        for _ in range(3):
            started = time.perf_counter()
            r = client.post("/api/v1/chat", json={"message": QUESTION, "context": SIG_CTX},
                            headers={"Authorization": f"Bearer {tok}"})
            timings.append(time.perf_counter() - started)
            assert r.status_code == 200 and r.json()["answer"]["summary"]
    assert max(timings) < config.agent()["llm"]["timeout_seconds"]
    assert max(timings) < 10                                     # export never runs on the request path
    stopper = threading.Thread(target=tracer.shutdown, daemon=True)   # bounded by the export timeout
    stopper.start()
    stopper.join(timeout=30)
