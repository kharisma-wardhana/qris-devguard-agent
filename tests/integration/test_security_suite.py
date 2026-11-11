"""§22.5 gaps not already proven elsewhere (p7 spec AC #21, #22). The full matrix maps every §22.5 bullet to the
tests that prove it: evals/datasets/security_cases.yaml, run by scripts/eval_security.py.

- secret in the prompt: no synthetic secret in any table (threads, approvals, tickets, audit, checkpoints), in the
  captured API and MCP server logs, or in any response; real subprocesses on the throwaway roles;
- every unauthorized write path against the real MCP server: zero tickets;
- SQL-like ticket text is stored literally through the approved path; the tables survive;
- invariant: every ticket in the schema has an executed approval decided by someone other than its requester.
Synthetic data only; IP strings built from parts.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
import sys
import uuid
from pathlib import Path

import httpx
import psycopg
import pytest
import yaml

from app.agent.harness import free_port
from app.agent.tools import ToolClient
from app.db import actions, store
from app.mcp_server import auth
from app.validators import keys

ROOT = Path(__file__).resolve().parents[2]
SECRET = secrets.token_hex(24)
API_AUD = "qris-devguard-api"
pytestmark = pytest.mark.db

TOKEN_VALUE = "eyJhbGciOiJIUzI1NiJ9" + "SUITESYNTHTOKEN" + secrets.token_hex(4)
CLIENT_SECRET = "SuiteSecretSYN" + secrets.token_hex(4)
KEY_BODY = "SUITEKEYMATERIAL" + secrets.token_hex(4)
SECRETS = [TOKEN_VALUE, CLIENT_SECRET, KEY_BODY]


def _wait(proc, port, what, log):
    import socket
    import time
    deadline = time.time() + 90
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"{what} exited: {Path(log).read_text(encoding='utf-8')[-2000:]}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    proc.kill()
    pytest.fail(f"{what} did not start")


def _start(args, env, port, what, log):
    fh = open(log, "w", encoding="utf-8")            # a file, not a pipe: an unread pipe blocks the server
    proc = subprocess.Popen([sys.executable, *args], cwd=ROOT, env=env, stdout=fh, stderr=fh)
    _wait(proc, port, what, log)
    return proc, fh


def _stop(proc, fh):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    fh.close()


@pytest.fixture(scope="module")
def stack(migrated_db_url, db_roles, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("suite")
    wanted = yaml.safe_load((ROOT / "knowledge_base/fixtures/signatures/cases.yaml").read_text(encoding="utf-8"))["keys"]
    keys.generate(tmp / "keys", wanted)
    mcp_port, api_port = free_port(), free_port()
    env = {**os.environ, "MCP_AUTH_SECRET": SECRET, "DEVGUARD_TEST_KEYS_DIR": str(tmp / "keys"),
           "DATABASE_URL": migrated_db_url, "QDRANT_URL": f"http://127.0.0.1:{free_port()}",
           "DEVGUARD_TEST_BACKENDS": "1", **db_roles.env()}
    logs = {"mcp": tmp / "mcp.log", "api": tmp / "api.log"}
    mcp = _start([str(ROOT / "scripts/mcp_server.py"), "--port", str(mcp_port)], env, mcp_port, "mcp", logs["mcp"])
    api = _start([str(ROOT / "scripts/api_server.py"), "--port", str(api_port), "--mcp-url",
                  f"http://127.0.0.1:{mcp_port}/mcp", "--fake-llm"], env, api_port, "api", logs["api"])
    yield {"api": f"http://127.0.0.1:{api_port}", "mcp": f"http://127.0.0.1:{mcp_port}/mcp", "logs": logs}
    _stop(*api)
    _stop(*mcp)


def hdr(sub, role):
    return {"Authorization": f"Bearer {auth.mint(sub, role, secret=SECRET, audience=API_AUD)}"}


@pytest.fixture
def owner(migrated_db_url):
    with psycopg.connect(migrated_db_url, autocommit=True) as c:
        yield c


def ticket_count(owner) -> int:
    return owner.execute("select count(*) from sandbox_tickets").fetchone()[0]


# --- secret in the prompt (§22.5 last bullet; AC #22) ------------------------------------------------------

def test_secret_in_prompt_reaches_no_table_log_or_response(stack, owner, record_property):
    pem = "-" * 5 + "BEGIN PRIVATE KEY" + "-" * 5 + f"\n{KEY_BODY}\n" + "-" * 5 + "END PRIVATE KEY" + "-" * 5
    message = (f"please create a ticket: client_secret={CLIENT_SECRET}, Authorization: Bearer {TOKEN_VALUE}, "
               f"key {pem}")
    ctx = {"endpoint_id": "qr-mpm-generate", "spec_version": "0.1.0",
           "payload": {"headers": {"Authorization": f"Bearer {TOKEN_VALUE}", "X-SIGNATURE": "c2lnbmF0dXJl"},
                       "body": {"client_secret": CLIENT_SECRET, "password": CLIENT_SECRET}}}
    base, dev, sup = stack["api"], hdr("suite-dev", "developer"), hdr("suite-sup", "support_engineer")
    tid = httpx.post(f"{base}/api/v1/threads", headers=dev, timeout=30).json()["thread_id"]
    out = httpx.post(f"{base}/api/v1/threads/{tid}/messages", json={"message": message, "context": ctx}, headers=dev,
                     timeout=120)
    assert out.status_code == 200 and out.json()["status"] == "awaiting_approval"
    decided = httpx.post(f"{base}/api/v1/approvals/{out.json()['approval']['approval_id']}/decision",
                         json={"decision": "approve"}, headers=sup, timeout=120)
    chat = httpx.post(f"{base}/api/v1/chat", json={"message": message, "context": ctx}, headers=dev, timeout=120)
    assert decided.status_code == 200 and chat.status_code == 200
    responses = out.text + decided.text + chat.text
    dump = b""
    for table in ("agent_threads", "approval_requests", "sandbox_tickets", "tool_audit_logs", "checkpoints",
                  "checkpoint_blobs", "checkpoint_writes"):
        for row in owner.execute(f"select * from {table}").fetchall():              # noqa: S608 - fixed names
            for v in row:
                dump += bytes(v) if isinstance(v, (bytes, memoryview)) else str(v).encode("utf-8", "replace")
    logs = "".join(p.read_text(encoding="utf-8", errors="replace") for p in stack["logs"].values())
    assert dump and logs, "nothing stored or logged: the check would prove nothing"
    leaks = [s for s in SECRETS if s.encode() in dump or s in logs or s in responses]
    record_property("actual", f"{len(SECRETS)} synthetic secrets; found in tables/logs/responses: {len(leaks)}")
    assert leaks == []


# --- every unauthorized write path against the real MCP server (zero side effects) -------------------------

PAYLOAD = {"title": "Suite unauthorized write", "body": "SYN: suite case", "evidence_ids": ["CIT-000001"],
           "priority": "low"}


def _approval(owner_url, *, requester="suite-req", decide="approve", payload=PAYLOAD, expired=False):
    with store.connect(owner_url) as c:
        tid = secrets.token_urlsafe(24)
        store.create_thread(c, tid, requester)
        a = store.get_or_create_pending(c, thread_id=tid, run_id=str(uuid.uuid4()), requester_id=requester,
                                        requester_role="developer", payload=payload, ttl_minutes=30)
        if decide:
            store.decide(c, str(a["approval_id"]), "suite-sup", decide)
        if expired:
            c.execute("update approval_requests set expires_at = now() - interval '1 minute' where approval_id = %s",
                      (a["approval_id"],))
        return tid, str(a["approval_id"])


def _args(tid, aid, payload=PAYLOAD, **over):
    return {"approval_id": aid, "thread_id": tid, **payload, "idempotency_key": actions.idempotency_key(tid, payload),
            **over}


def test_unauthorized_write_paths_create_nothing(stack, migrated_db_url, owner, record_property):
    url = stack["mcp"]
    ok_tid, ok_aid = _approval(migrated_db_url)
    pend_tid, pend_aid = _approval(migrated_db_url, decide=None)
    rej_tid, rej_aid = _approval(migrated_db_url, decide="reject")
    exp_tid, exp_aid = _approval(migrated_db_url, expired=True)
    other_tid, _ = _approval(migrated_db_url)
    changed = {**PAYLOAD, "priority": "high"}
    attempts = [
        ("no approval (random id)", "suite-req", "developer", _args(ok_tid, str(uuid.uuid4())), "APPROVAL_INVALID"),
        ("kb_maintainer", "suite-req", "kb_maintainer", _args(ok_tid, ok_aid), "FORBIDDEN"),
        ("another user's approval", "suite-other", "developer", _args(ok_tid, ok_aid), "APPROVAL_INVALID"),
        ("approval moved to another thread", "suite-req", "developer",
         {**_args(ok_tid, ok_aid), "thread_id": other_tid,
          "idempotency_key": actions.idempotency_key(other_tid, PAYLOAD)}, "APPROVAL_INVALID"),
        ("changed payload", "suite-req", "developer",
         {**_args(ok_tid, ok_aid, changed)}, "APPROVAL_MISMATCH"),
        ("pending approval", "suite-req", "developer", _args(pend_tid, pend_aid), "APPROVAL_NOT_USABLE"),
        ("rejected approval", "suite-req", "developer", _args(rej_tid, rej_aid), "APPROVAL_NOT_USABLE"),
        ("expired approval", "suite-req", "developer", _args(exp_tid, exp_aid), "APPROVAL_EXPIRED"),
        ("forged idempotency key", "suite-req", "developer", _args(ok_tid, ok_aid, idempotency_key="f" * 64),
         "INVALID_ARGUMENT"),
    ]
    before = ticket_count(owner)
    seen = []

    async def run(sub, role, args):
        client = ToolClient(url, SECRET)
        client.retries = 0
        (res,) = await client.call(sub, role, [("create_support_ticket", args)])
        return res
    for name, sub, role, args, want in attempts:
        res = asyncio.run(run(sub, role, args))
        seen.append((name, res.error_code))
        assert res.error_code == want, (name, res.data)
    record_property("actual", "; ".join(f"{n} -> {c}" for n, c in seen) + f"; tickets created: "
                    f"{ticket_count(owner) - before}")
    assert ticket_count(owner) == before
    # the legitimate call still works, exactly once, and a replay returns the same ticket
    first = asyncio.run(run("suite-req", "developer", _args(ok_tid, ok_aid)))
    again = asyncio.run(run("suite-req", "developer", _args(ok_tid, ok_aid)))
    assert first.ok and again.ok and again.data["status"] == "already_created"
    assert again.data["ticket_id"] == first.data["ticket_id"] and ticket_count(owner) == before + 1


# --- SQL-like text is data -------------------------------------------------------------------------------

def test_sql_like_ticket_text_is_stored_literally(stack, migrated_db_url, owner):
    nasty = {"title": "x'); DROP TABLE sandbox_tickets; --", "body": "1 OR 1=1; DELETE FROM approval_requests; --",
             "evidence_ids": ["CIT-000002"], "priority": "low"}
    tid, aid = _approval(migrated_db_url, payload=nasty)

    async def run():
        client = ToolClient(stack["mcp"], SECRET)
        client.retries = 0
        (res,) = await client.call("suite-req", "developer", [("create_support_ticket", _args(tid, aid, nasty))])
        return res
    res = asyncio.run(run())
    assert res.ok, res.data
    row = owner.execute("select title, body_sanitized from sandbox_tickets where thread_id = %s", (tid,)).fetchone()
    assert row == (nasty["title"], nasty["body"])
    assert owner.execute("select count(*) from approval_requests").fetchone()[0] > 0


# --- invariant: no ticket without a valid, four-eyes approval ---------------------------------------------

def test_every_ticket_has_an_executed_four_eyes_approval(stack, owner, record_property):
    rows = owner.execute(
        "select t.ticket_id, a.status, a.approver_id, a.requester_id, a.ticket_id = t.ticket_id "
        "from sandbox_tickets t left join approval_requests a on a.approval_id = t.approval_id").fetchall()
    bad = [r for r in rows if r[1] != "executed" or r[2] is None or r[2] == r[3] or not r[4]]
    record_property("actual", f"{len(rows)} tickets in the test schema; without an executed four-eyes approval: "
                              f"{len(bad)}")
    assert rows and bad == []


def test_secrets_list_is_not_vacuous():
    assert all(len(s) > 20 for s in SECRETS) and json.dumps(SECRETS)
