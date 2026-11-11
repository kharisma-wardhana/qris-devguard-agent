"""TRD §22.4 crash/resume with real processes (p6 spec AC #10, #11).

MCP server + API (fake LLM) as subprocesses on a migrated throwaway schema of the local PostgreSQL:
escalate -> awaiting approval -> KILL the API -> start a new API process -> the thread shows the same run and
pending approval -> approve -> exactly one ticket -> second decision 409, resume 409, still one ticket.
Then a run carrying secret patterns, and a dump of every table and checkpoint looking for them.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psycopg
import pytest
import yaml

from app.agent import config
from app.agent.harness import free_port
from app.db import actions, store
from app.mcp_server import auth
from app.validators import keys

ROOT = Path(__file__).resolve().parents[2]
SECRET = "c" * 48
API_AUD = config.agent()["api"]["audience"]
SIG_CTX = {"api_family": "snap", "signature": {"algorithm": "SHA256withRSA", "fixture_id": "syn-rsa-01",
                                               "canonicalization_profile": "snap-asymmetric-access-token-v1",
                                               "test_case_ref": "sig-at-wrong-separator"}}
ESCALATE = {"message": "My access-token signature keeps failing, please create a ticket for this.", "context": SIG_CTX}


def _wait(proc, port, what):
    deadline = time.time() + 90
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"{what} exited: {proc.stderr.read()}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    proc.kill()
    pytest.fail(f"{what} did not start")


def _start(args, env, port, what):
    proc = subprocess.Popen([sys.executable, *args], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8")
    _wait(proc, port, what)
    return proc


def _stop(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="module")
def stack(migrated_db_url, db_roles, tmp_path_factory):
    keys_dir = tmp_path_factory.mktemp("keys")
    wanted = yaml.safe_load((ROOT / "knowledge_base/fixtures/signatures/cases.yaml").read_text(encoding="utf-8"))["keys"]
    keys.generate(keys_dir, wanted)
    mcp_port = free_port()
    env = {**os.environ, "MCP_AUTH_SECRET": SECRET, "DEVGUARD_TEST_KEYS_DIR": str(keys_dir), "DATABASE_URL": migrated_db_url,
           "QDRANT_URL": f"http://127.0.0.1:{free_port()}", "DEVGUARD_TEST_BACKENDS": "1",
           **db_roles.env()}                                   # p7: throwaway runtime roles
    mcp = _start([str(ROOT / "scripts/mcp_server.py"), "--port", str(mcp_port)], env, mcp_port, "mcp server")

    def api(port):
        return _start([str(ROOT / "scripts/api_server.py"), "--port", str(port), "--mcp-url",
                       f"http://127.0.0.1:{mcp_port}/mcp", "--fake-llm"], env, port, "api server")
    yield api
    _stop(mcp)


def hdr(sub, role):
    return {"Authorization": f"Bearer {auth.mint(sub, role, secret=SECRET, audience=API_AUD)}"}


DEV, SUP, SUP2 = hdr("dev-1", "developer"), hdr("sup-1", "support_engineer"), hdr("sup-2", "support_engineer")


@pytest.mark.db
def test_crash_resume_exactly_one_ticket(stack, migrated_db_url, capsys):
    port1 = free_port()
    api1 = stack(port1)
    base = f"http://127.0.0.1:{port1}"
    tid = httpx.post(f"{base}/api/v1/threads", headers=DEV, timeout=30).json()["thread_id"]
    first = httpx.post(f"{base}/api/v1/threads/{tid}/messages", json=ESCALATE, headers=DEV, timeout=120).json()
    assert first["status"] == "awaiting_approval", first
    run_id, aid = first["run_id"], first["approval"]["approval_id"]

    api1.kill()                                                     # crash: no graceful shutdown
    api1.wait(timeout=10)

    port2 = free_port()
    api2 = stack(port2)
    base = f"http://127.0.0.1:{port2}"
    try:
        thread = httpx.get(f"{base}/api/v1/threads/{tid}", headers=DEV, timeout=30).json()
        assert thread["status"] == "awaiting_approval" and thread["run_id"] == run_id
        assert thread["approval"]["approval_id"] == aid
        done = httpx.post(f"{base}/api/v1/approvals/{aid}/decision", json={"decision": "approve"}, headers=SUP,
                          timeout=120)
        assert done.status_code == 200, done.text
        assert done.json()["run"]["answer"]["action"]["status"] == "created"
        again = httpx.post(f"{base}/api/v1/approvals/{aid}/decision", json={"decision": "approve"}, headers=SUP2, timeout=30)
        resumed = httpx.post(f"{base}/api/v1/threads/{tid}/resume", headers=DEV, timeout=30)
        assert again.status_code == 409 and resumed.status_code == 409
        with store.connect(migrated_db_url) as c:
            appr = store.get_approval(c, aid)
            key = actions.idempotency_key(tid, dict(appr["action_payload"]))
            n = store.count_tickets(c, key)
            total = c.execute("select count(*) as n from sandbox_tickets where thread_id = %s", (tid,)).fetchone()["n"]
        assert n == total == 1 and appr["status"] == "executed"
        evidence = {"thread_id": tid, "run_id": run_id, "approval_id": aid, "ticket_id": str(appr["ticket_id"]),
                    "api_killed_pid": api1.pid, "api_restarted_pid": api2.pid, "tickets_for_key": n,
                    "second_decision": again.status_code, "second_resume": resumed.status_code}
        with capsys.disabled():
            print("\nCRASH-RESUME EVIDENCE " + json.dumps(evidence))
    finally:
        _stop(api2)


@pytest.mark.db
def test_approver_must_be_another_person(stack):
    port = free_port()
    api = stack(port)
    base = f"http://127.0.0.1:{port}"
    try:
        tid = httpx.post(f"{base}/api/v1/threads", headers=SUP, timeout=30).json()["thread_id"]
        out = httpx.post(f"{base}/api/v1/threads/{tid}/messages", json=ESCALATE, headers=SUP, timeout=120).json()
        aid = out["approval"]["approval_id"]
        own = httpx.post(f"{base}/api/v1/approvals/{aid}/decision", json={"decision": "approve"}, headers=SUP, timeout=30)
        other = httpx.post(f"{base}/api/v1/approvals/{aid}/decision", json={"decision": "approve"}, headers=SUP2, timeout=120)
        assert own.status_code == 403 and other.status_code == 200
    finally:
        _stop(api)


SECRETS = ["SuperSecretSYN123", "eyJhbGciOiJIUzI1NiJ9SYNTHETICTOKENVALUE0123", "SYNTHETICKEYMATERIALxyz",
           "c2lnbmF0dXJlLXZhbHVlLXN5bg=="]


@pytest.mark.db
def test_no_secret_in_any_table_or_checkpoint(stack, migrated_db_url):
    pem = "-" * 5 + "BEGIN PRIVATE KEY" + "-" * 5 + "\nSYNTHETICKEYMATERIALxyz\n" + "-" * 5 + "END PRIVATE KEY" + "-" * 5
    message = (f"please create a ticket: my client_secret=SuperSecretSYN123 and Authorization: Bearer "
               f"eyJhbGciOiJIUzI1NiJ9SYNTHETICTOKENVALUE0123 and key {pem}")
    ctx = {"endpoint_id": "qr-mpm-generate", "spec_version": "0.1.0",
           "payload": {"headers": {"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9SYNTHETICTOKENVALUE0123",
                                   "X-SIGNATURE": "c2lnbmF0dXJlLXZhbHVlLXN5bg=="}, "body": {"client_secret": "SuperSecretSYN123"}}}
    port = free_port()
    api = stack(port)
    base = f"http://127.0.0.1:{port}"
    try:
        tid = httpx.post(f"{base}/api/v1/threads", headers=DEV, timeout=30).json()["thread_id"]
        out = httpx.post(f"{base}/api/v1/threads/{tid}/messages", json={"message": message, "context": ctx}, headers=DEV,
                         timeout=120).json()
        assert out["status"] == "awaiting_approval"
        r = httpx.post(f"{base}/api/v1/approvals/{out['approval']['approval_id']}/decision", json={"decision": "approve"},
                       headers=SUP, timeout=120)
        assert r.status_code == 200 and r.json()["run"]["answer"]["action"]["status"] == "created"
        for s in SECRETS:
            assert s not in json.dumps(out) + r.text
    finally:
        _stop(api)
    dump = b""
    with psycopg.connect(migrated_db_url) as c:
        for table in ("agent_threads", "approval_requests", "sandbox_tickets", "checkpoints", "checkpoint_blobs",
                      "checkpoint_writes"):
            for row in c.execute(f"select * from {table}").fetchall():          # noqa: S608 - fixed table names
                for v in row:
                    dump += bytes(v) if isinstance(v, (bytes, memoryview)) else str(v).encode("utf-8", "replace")
    assert dump, "nothing was stored"
    for s in SECRETS:
        assert s.encode() not in dump, s
