"""p6 agent changes: evidence ordering (AC #12) and the HITL graph (AC #3, #6, #9, #14)."""

from __future__ import annotations

from app.agent.graph import order_evidence


def c(rank, doc, section):
    return {"citation_id": f"CIT-{rank:06d}", "rank": rank, "document_id": doc, "section_id": section}


SNAP = "snap-technical-security-standard"
DATA = "snap-data-technical-spec"
MPM = "synthetic-qris-mpm-service-spec"
SOP = "synthetic-qris-troubleshooting-sop"

# The live flow-1 pool (p5 agent-flow-report): 2.1.5 sat at ranks 7, 10, 11 behind 6.1 and 2.1.6#2.
FLOW1 = [c(5, DATA, "6.1"), c(6, SNAP, "2.1.6#2"), c(7, SNAP, "2.1.5"), c(9, SNAP, "2.1.6"), c(10, SNAP, "2.1.5"),
         c(11, SNAP, "2.1.5")]


def ids(chunks):
    return [x["rank"] for x in chunks]


def test_flow1_profile_puts_2_1_5_first():
    out = order_evidence(FLOW1, api_family="snap", endpoint_id=None, profile="snap-asymmetric-access-token-v1")
    assert ids(out) == [7, 10, 11, 5, 6, 9]


def test_endpoint_section_prefix_is_bounded():
    pool = [c(1, SOP, "1"), c(2, MPM, "20.x"), c(3, MPM, "3.request-fields"), c(4, MPM, "2.request-fields"), c(5, MPM, "2")]
    out = order_evidence(pool, api_family=None, endpoint_id="qr-mpm-generate", profile=None)
    assert ids(out) == [4, 5, 2, 3, 1]                     # "20.x" is not section 2


def test_family_only_puts_family_documents_first():
    pool = [c(1, SOP, "1"), c(2, MPM, "3.request-fields"), c(3, SOP, "2"), c(4, MPM, "1.3")]
    assert ids(order_evidence(pool, api_family="qris-mpm", endpoint_id=None, profile=None)) == [2, 4, 1, 3]


def test_no_family_no_endpoint_keeps_fused_order():
    assert ids(order_evidence(FLOW1, api_family=None, endpoint_id=None, profile=None)) == ids(FLOW1)


def test_unknown_profile_or_endpoint_falls_back_to_family():
    out = order_evidence(FLOW1, api_family="snap", endpoint_id=None, profile="synthetic-snap-symmetric-v1")
    assert ids(out) == ids(FLOW1)                          # every chunk is a SNAP-family doc: fused order kept


# --- HITL graph on PostgreSQL (step 5) -------------------------------------------------------------

import asyncio  # noqa: E402

import pytest  # noqa: E402

from app.agent.graph import Agent  # noqa: E402
from app.agent.harness import McpHarness  # noqa: E402
from app.agent.llm import FakeLLM  # noqa: E402
from app.agent.tools import ToolClient  # noqa: E402
from app.db import store  # noqa: E402

SIG_CTX = {"api_family": "snap", "signature": {"algorithm": "SHA256withRSA", "fixture_id": "syn-rsa-01",
                                               "canonicalization_profile": "snap-asymmetric-access-token-v1",
                                               "test_case_ref": "sig-at-wrong-separator"}}
ESCALATE = "My access-token signature keeps failing, please create a ticket for this."


@pytest.fixture(scope="module")
def harness(migrated_db_url, db_roles):
    # p7: the write tool fails closed without an audit trail, so the harness gets the throwaway audit role
    with McpHarness(db_conninfo=migrated_db_url, audit_conninfo=db_roles.url("audit")) as h:
        yield h


def with_agent(url, harness, fn, llm=None):
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async def go():
        async with AsyncPostgresSaver.from_conn_string(url) as saver:
            agent = Agent(ToolClient(harness.url, harness.secret), llm or FakeLLM(), checkpointer=saver, db_conninfo=url)
            return await fn(agent)
    return asyncio.run(go())


def db(url, fn, *a):
    with store.connect(url) as c:
        return fn(c, *a)


def counts(url, thread_id):
    def q(c):
        a = c.execute("select count(*) as n from approval_requests where thread_id = %s", (thread_id,)).fetchone()["n"]
        t = c.execute("select count(*) as n from sandbox_tickets where thread_id = %s", (thread_id,)).fetchone()["n"]
        return a, t
    return db(url, q)


def escalate(url, harness, role="developer", sub="dev-1"):
    return with_agent(url, harness, lambda a: a.run(sub=sub, role=role, message=ESCALATE, context=SIG_CTX))


@pytest.mark.db
def test_escalation_pauses_with_one_pending_approval_and_no_ticket(migrated_db_url, harness):
    out = escalate(migrated_db_url, harness)
    assert out["status"] == "awaiting_approval" and out["approval"]["status"] == "pending"
    assert [t["tool"] for t in out["tool_calls"]] == ["search_qris_spec", "validate_test_signature"]
    assert counts(migrated_db_url, out["thread_id"]) == (1, 0)
    payload = out["approval"]["action"]["payload"]
    assert payload["title"].startswith("Escalation: ") and "VAL-0" in payload["evidence_ids"]
    assert db(migrated_db_url, store.thread_owner, out["thread_id"]) == "dev-1"


@pytest.mark.db
def test_approve_then_resume_creates_exactly_one_ticket(migrated_db_url, harness):
    out = escalate(migrated_db_url, harness)
    aid = out["approval"]["approval_id"]
    assert db(migrated_db_url, store.decide, aid, "sup-1", "approve")[0] == store.DECIDED
    done = with_agent(migrated_db_url, harness, lambda a: a.resume(out["thread_id"]))
    assert done["status"] == "completed" and done["action_result"]["status"] == "created"
    assert done["answer"]["action"]["ticket_id"] == done["action_result"]["ticket_id"]
    assert counts(migrated_db_url, out["thread_id"]) == (1, 1)
    assert db(migrated_db_url, store.get_approval, aid)["status"] == "executed"
    assert db(migrated_db_url, store.get_thread, out["thread_id"], "dev-1")["status"] == "completed"


@pytest.mark.db
def test_reject_creates_nothing(migrated_db_url, harness):
    out = escalate(migrated_db_url, harness)
    db(migrated_db_url, store.decide, out["approval"]["approval_id"], "sup-1", "reject")
    done = with_agent(migrated_db_url, harness, lambda a: a.resume(out["thread_id"]))
    assert done["action_result"]["status"] == "rejected" and counts(migrated_db_url, out["thread_id"]) == (1, 0)
    assert "rejected" in " ".join(done["answer"]["limitations"])


@pytest.mark.db
def test_expired_approval_creates_nothing(migrated_db_url, harness):
    out = escalate(migrated_db_url, harness)
    aid = out["approval"]["approval_id"]

    def expire(c):
        with c.transaction():
            c.execute("update approval_requests set expires_at = now() - interval '1 second' where approval_id = %s", (aid,))
    db(migrated_db_url, expire)
    assert db(migrated_db_url, store.decide, aid, "sup-1", "approve")[0] == store.EXPIRED
    done = with_agent(migrated_db_url, harness, lambda a: a.resume(out["thread_id"]))
    assert done["action_result"]["status"] == "expired" and counts(migrated_db_url, out["thread_id"]) == (1, 0)


@pytest.mark.db
def test_crash_between_insert_and_checkpoint_resumes_to_the_same_ticket(migrated_db_url, harness):
    out = escalate(migrated_db_url, harness)
    db(migrated_db_url, store.decide, out["approval"]["approval_id"], "sup-1", "approve")

    async def crash_after_ticket(agent):
        real = agent.tools.call

        async def call(sub, role, calls):
            res = await real(sub, role, calls)
            if calls and calls[0][0] == "create_support_ticket":
                raise RuntimeError("simulated crash after the insert, before the checkpoint")
            return res
        agent.tools.call = call
        with pytest.raises(RuntimeError):
            await agent.resume(out["thread_id"])
    with_agent(migrated_db_url, harness, crash_after_ticket)
    assert counts(migrated_db_url, out["thread_id"]) == (1, 1)                  # inserted, graph never checkpointed
    done = with_agent(migrated_db_url, harness, lambda a: a.resume(out["thread_id"]))
    assert done["action_result"]["status"] == "already_created" and counts(migrated_db_url, out["thread_id"]) == (1, 1)
    first = db(migrated_db_url, store.get_approval, out["approval"]["approval_id"])["ticket_id"]
    assert done["action_result"]["ticket_id"] == str(first)


@pytest.mark.db
def test_state_survives_a_new_agent_instance(migrated_db_url, harness):
    out = escalate(migrated_db_url, harness)
    snap = with_agent(migrated_db_url, harness, lambda a: a.snapshot(out["thread_id"]))   # fresh saver + agent
    assert snap["status"] == "awaiting_approval" and snap["run_id"] == out["run_id"]
    assert snap["approval"]["approval_id"] == out["approval"]["approval_id"]


@pytest.mark.db
def test_kb_maintainer_escalation_is_draft_only(migrated_db_url, harness):
    out = escalate(migrated_db_url, harness, role="kb_maintainer", sub="kb-1")
    assert out["status"] == "completed" and out["approval"] is None
    assert out["answer"]["draft"]["status"] == "draft_only" and counts(migrated_db_url, out["thread_id"]) == (0, 0)


@pytest.mark.db
def test_low_risk_run_with_store_is_unchanged(migrated_db_url, harness):
    out = with_agent(migrated_db_url, harness, lambda a: a.run(sub="dev-1", role="developer",
                                                               message="why is my signature rejected?", context=SIG_CTX))
    assert out["status"] == "completed" and out["approval"] is None and counts(migrated_db_url, out["thread_id"]) == (0, 0)


def test_no_secret_in_any_checkpoint_of_the_history():
    """p6 step-7 regression: LangGraph checkpoints the raw input before any node runs, so run() sanitizes first.
    P5's test only looked at the final state; this walks every checkpoint and pending write (in-memory saver)."""
    import json as _json

    from app.agent import answer as _answer

    pem = "-" * 5 + "BEGIN PRIVATE KEY" + "-" * 5 + "\nSYNTHETICKEYMATERIALxyz\n" + "-" * 5 + "END PRIVATE KEY" + "-" * 5
    msg = f"why invalid? client_secret=SuperSecretSYN123 {pem}"
    with McpHarness() as h:
        agent = Agent(ToolClient(h.url, h.secret), FakeLLM())
        out = asyncio.run(agent.run(sub="dev-1", role="developer", message=msg, context=SIG_CTX))
    history = list(agent.checkpointer.list({"configurable": {"thread_id": out["thread_id"]}}))
    assert len(history) > 3
    blob = _json.dumps([(t.checkpoint, t.pending_writes) for t in history], default=str)
    for s in ("SuperSecretSYN123", "SYNTHETICKEYMATERIALxyz"):
        assert s not in blob
    assert out["answer"]["limitations"][0] == _answer.KEY_WARNING        # the flag survives the early sanitize
