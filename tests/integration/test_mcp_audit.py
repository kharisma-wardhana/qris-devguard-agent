"""Audit trail of the real MCP server subprocess on the throwaway audit role (p7 spec AC #1, #2, #3, #5)."""

from __future__ import annotations

import asyncio
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
from app.agent.tools import ToolClient, current_run_id
from app.db import audit
from app.validators import keys

ROOT = Path(__file__).resolve().parents[2]
SECRET = secrets.token_hex(24)
LEAK = "SYNleak" + secrets.token_hex(6)
pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def mcp(migrated_db_url, db_roles, tmp_path_factory):
    keys_dir = tmp_path_factory.mktemp("keys")
    wanted = yaml.safe_load((ROOT / "knowledge_base/fixtures/signatures/cases.yaml").read_text(encoding="utf-8"))["keys"]
    keys.generate(keys_dir, wanted)
    port = free_port()
    env = {**os.environ, "MCP_AUTH_SECRET": SECRET, "DEVGUARD_TEST_KEYS_DIR": str(keys_dir),
           "QDRANT_URL": f"http://127.0.0.1:{free_port()}", "DATABASE_URL": migrated_db_url, **db_roles.env()}
    proc = subprocess.Popen([sys.executable, str(ROOT / "scripts/mcp_server.py"), "--port", str(port)], cwd=ROOT,
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    from tests.integration.test_crash_resume import _wait
    _wait(proc, port, "mcp server")
    yield f"http://127.0.0.1:{port}/mcp", proc
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
def owner(migrated_db_url):
    with psycopg.connect(migrated_db_url, autocommit=True) as c:
        yield c


def calls(url, sub, role, batch, run_id=None):
    async def go():
        current_run_id.set(run_id)
        client = ToolClient(url, SECRET)
        client.retries = 0
        return await client.call(sub, role, batch)
    return asyncio.run(go())


LOOKUP = ("lookup_response_code", {"code": "4094700", "api_family": "qris-mpm", "spec_version": "0.1.0"})
SIG = ("validate_test_signature", {"algorithm": "HMAC_SHA512", "fixture_id": "syn-hmac-01",
                                   "canonicalization_profile": "synthetic-snap-symmetric-v1",
                                   "test_payload_ref": "sig-gen-body-not-minified"})


def test_every_outcome_writes_one_matching_row_with_run_id(mcp, owner):
    url, _ = mcp
    run_id = str(uuid.uuid4())
    batch = [LOOKUP, ("lookup_response_code", {**LOOKUP[1], "code": "../../etc"}), ("search_qris_spec", {"query": "x"})]
    results = calls(url, "aud-sub", "developer", batch, run_id)
    forbidden = calls(url, "aud-kb", "kb_maintainer", [SIG], run_id)
    want = [("lookup_response_code", "OK"), ("lookup_response_code", "INVALID_ARGUMENT"),
            ("search_qris_spec", "SEARCH_UNAVAILABLE"), ("validate_test_signature", "FORBIDDEN")]
    for res, (tool, outcome), role in zip([*results, *forbidden], want,
                                          ["developer"] * 3 + ["kb_maintainer"]):
        rows = audit.rows(owner, correlation_id=res.data["correlation_id"])
        assert [(r["tool"], r["phase"], r["outcome"], r["actor_role"]) for r in rows] == [
            (tool, "result", outcome, role)], res.data
        assert str(rows[0]["run_id"]) == run_id


def test_bad_run_id_header_is_dropped(mcp, owner):
    url, _ = mcp
    (res,) = calls(url, "aud-sub", "developer", [LOOKUP], "'; DROP TABLE tool_audit_logs; --")
    (row,) = audit.rows(owner, correlation_id=res.data["correlation_id"])
    assert row["run_id"] is None


@pytest.mark.parametrize("token", [None, "garbage", "Bearer-less"])
def test_unauthenticated_request_is_audited_as_refused(mcp, owner, token):
    url, _ = mcp
    before = len(audit.rows(owner, phase="refused", outcome="UNAUTHENTICATED"))
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = httpx.post(url, headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, timeout=10)
    assert r.status_code == 401
    rows = audit.rows(owner, phase="refused", outcome="UNAUTHENTICATED")
    assert len(rows) == before + 1
    assert (rows[-1]["actor_sub"], rows[-1]["actor_role"], rows[-1]["tool"]) == ("-", "-", None)


def test_secret_shaped_arguments_never_reach_the_audit_table(mcp, owner):
    url, proc = mcp
    secret_args = [
        ("lookup_response_code", {"code": f"client_secret={LEAK}", "api_family": "qris-mpm", "spec_version": "0.1.0"}),
        ("search_qris_spec", {"query": f"Authorization: Bearer {LEAK}{LEAK} password={LEAK}"}),
        ("validate_request_payload", {"api_family": "qris-mpm", "endpoint_id": "qr-mpm-query", "spec_version": "0.1.0",
                                      "payload": {"headers": {"X-SIGNATURE": LEAK}, "body": {"secret": LEAK}}}),
        ("validate_test_signature", {**SIG[1], "fixture_id": f"private_key={LEAK}"}),
        ("create_support_ticket", {"approval_id": str(uuid.uuid4()), "thread_id": "t" * 32, "title": LEAK,
                                   "body": f"access_token={LEAK}", "evidence_ids": [], "priority": "low",
                                   "idempotency_key": "0" * 64}),
    ]
    calls(url, "aud-leak", "developer", secret_args)
    dump = owner.execute("select string_agg(t::text, '|') from tool_audit_logs t").fetchone()[0]
    assert LEAK not in dump
    rows = [r for r in audit.rows(owner, actor_sub="aud-leak")]
    assert {r["tool"] for r in rows} >= {"lookup_response_code", "search_qris_spec", "create_support_ticket"}
