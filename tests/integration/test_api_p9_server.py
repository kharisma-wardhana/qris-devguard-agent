"""p9 routes on the real API subprocess: authentication and authorization (docs/p9-demo-ui-alerts/spec.md AC #1, #3, #4).

A dedicated server for this module: the burst test empties this server's per-subject bucket only (CLAUDE.md).
No database here (the routes that pass auth answer 503); owner scoping on PostgreSQL is in
tests/unit/test_api_p9_routes.py (-m db).
"""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from app.agent import config
from app.agent.harness import free_port
from app.mcp_server import auth

ROOT = Path(__file__).resolve().parents[2]
SECRET = secrets.token_hex(24)
API_AUD = config.agent()["api"]["audience"]


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


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("p9api")
    port = free_port()
    env = {**os.environ, "MCP_AUTH_SECRET": SECRET, "DEVGUARD_TEST_BACKENDS": "1", "DEVGUARD_TRACING": "0",
           "DEVGUARD_DB_DOTENV": str(tmp / "no.env"), "DEVGUARD_ALERT_STORE": str(tmp / "alerts.jsonl")}
    env.pop("DATABASE_URL", None)
    proc = subprocess.Popen([sys.executable, str(ROOT / "scripts/api_server.py"), "--port", str(port),
                             "--mcp-url", f"http://127.0.0.1:{free_port()}/mcp", "--fake-llm"], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    _wait(proc, port, "api server")
    yield f"http://127.0.0.1:{port}"
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def get(base, path, sub="dev-1", role="developer", token=None, **kw):
    tok = token if token is not None else auth.mint(sub, role, secret=SECRET, audience=API_AUD)
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    return httpx.get(f"{base}{path}", headers=headers, timeout=10, **kw)


ROUTES = ["/api/v1/threads", "/api/v1/approvals?status=pending", "/api/v1/alerts"]


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("name", ["none", "mcp audience", "other secret", "expired"])
def test_authentication(base, path, name):
    tok = {"none": "",
           "mcp audience": auth.mint("d", "developer", secret=SECRET),
           "other secret": auth.mint("d", "developer", secret=secrets.token_hex(24), audience=API_AUD),
           "expired": auth.mint("d", "developer", secret=SECRET, audience=API_AUD, ttl_minutes=1,
                                now=int(time.time()) - 3600)}[name]
    r = get(base, path, token=tok)
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize("role", ["developer", "kb_maintainer"])
def test_pending_approvals_forbidden_for_non_approvers(base, role):
    r = get(base, "/api/v1/approvals?status=pending", sub=f"p9-{role}", role=role)
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"


@pytest.mark.parametrize("role", ["support_engineer", "admin"])
def test_pending_approvals_pass_authorization_for_approvers(base, role):
    r = get(base, "/api/v1/approvals?status=pending", sub=f"p9-{role}", role=role)
    assert r.status_code == 503 and r.json()["code"] == "SERVICE_UNAVAILABLE"     # past auth; no store here


def test_thread_list_burst_is_rate_limited(base):
    """api_other: burst 20 per subject (configs/security.yaml)."""
    codes = [get(base, "/api/v1/threads", sub="p9-burst").status_code for _ in range(30)]
    assert 429 in codes and codes[0] != 429
    r = get(base, "/api/v1/threads", sub="p9-burst")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1


@pytest.mark.parametrize("role", ["developer", "kb_maintainer"])
def test_alerts_forbidden_for_non_approvers(base, role):
    r = get(base, "/api/v1/alerts", sub=f"p9-a-{role}", role=role)
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"


@pytest.mark.parametrize("role", ["support_engineer", "admin"])
def test_alerts_for_approvers_with_an_empty_store(base, role):
    r = get(base, "/api/v1/alerts", sub=f"p9-a-{role}", role=role)
    assert r.status_code == 200 and r.json() == {"alerts": [], "skipped": 0}
