"""p9 ui-refresh routes on the real API subprocess: authentication, authorization and the rate limit
(docs/p9-ui-refresh/spec.md AC #1, #2, #3, #4).

A dedicated server for this module: tests/integration/test_api_p9_server.py already spends about half of the
per-address burst (all callers are 127.0.0.1), and the burst test here empties this server's bucket only (CLAUDE.md).
The release-gate route reads a temp directory (DEVGUARD_REPORTS_DIR), because evals/reports/ is git-ignored.
"""

from __future__ import annotations

import json
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
from app.api import reports
from app.mcp_server import auth

ROOT = Path(__file__).resolve().parents[2]
SECRET = secrets.token_hex(24)
API_AUD = config.agent()["api"]["audience"]
SOURCES, GATE = "/api/v1/sources", "/api/v1/evaluations/release-gate"
REPORT = {"generated_at": "2026-11-16T00:00:00+00:00", "verdict": "FAIL", "not_passing": ["g2"],
          "critical_not_passing": [], "freshness_days": 14, "reported_not_gated": {"latency": {}},
          "gates": [{"name": "g1", "kind": "critical", "status": "PASS", "value": 0, "threshold": {"max": 0},
                     "evidence": [{"path": "evals/reports/x.json", "sha256": "cd" * 32, "date": "2026-11-15"}],
                     "note": ""},
                    {"name": "g2", "kind": "gate", "status": "FAIL", "value": 0.27, "threshold": {"min": 0.9},
                     "evidence": [], "note": ""}]}


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
    tmp = tmp_path_factory.mktemp("uirefresh")
    (tmp / "release-gate-2026-11-16T000000.json").write_text(json.dumps(REPORT), encoding="utf-8")
    port = free_port()
    env = {**os.environ, "MCP_AUTH_SECRET": SECRET, "DEVGUARD_TEST_BACKENDS": "1", "DEVGUARD_TRACING": "0",
           "DEVGUARD_DB_DOTENV": str(tmp / "no.env"), "DEVGUARD_ALERT_STORE": str(tmp / "alerts.jsonl"),
           reports.REPORTS_DIR_ENV: str(tmp)}
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


def get(base, path, sub="dev-1", role="developer", token=None):
    tok = token if token is not None else auth.mint(sub, role, secret=SECRET, audience=API_AUD)
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    return httpx.get(f"{base}{path}", headers=headers, timeout=10)


@pytest.mark.parametrize("path", [SOURCES, GATE])
@pytest.mark.parametrize("name", ["none", "mcp audience", "other secret", "expired"])
def test_authentication(base, path, name):
    tok = {"none": "",
           "mcp audience": auth.mint("d", "developer", secret=SECRET),
           "other secret": auth.mint("d", "developer", secret=secrets.token_hex(24), audience=API_AUD),
           "expired": auth.mint("d", "developer", secret=SECRET, audience=API_AUD, ttl_minutes=1,
                                now=int(time.time()) - 3600)}[name]
    r = get(base, path, token=tok)
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED"
    assert "sources" not in r.json() and "gates" not in r.json()


@pytest.mark.parametrize("role", ["developer", "support_engineer", "kb_maintainer", "admin"])
def test_sources_for_every_role(base, role):
    r = get(base, SOURCES, sub=f"ui-s-{role}", role=role)
    assert r.status_code == 200 and r.json() == {"sources": reports.sources()}


@pytest.mark.parametrize("role", ["developer", "support_engineer", "kb_maintainer", "admin"])
def test_release_gate_for_every_role(base, role):
    r = get(base, GATE, sub=f"ui-g-{role}", role=role)
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "FAIL" and [g["name"] for g in body["gates"]] == ["g1", "g2"]
    assert body["gates"][0]["evidence_date"] == "2026-11-15"
    assert "sha256" not in r.text and "evals/reports" not in r.text and "freshness_days" not in r.text


def test_sources_burst_is_rate_limited(base):
    """api_other: burst 20 per subject (configs/security.yaml); the two routes share the bucket."""
    codes = [get(base, SOURCES if n % 2 else GATE, sub="ui-burst").status_code for n in range(30)]
    assert 429 in codes and codes[0] != 429
    r = get(base, SOURCES, sub="ui-burst")
    assert r.status_code == 429 and r.json()["code"] == "RATE_LIMITED" and int(r.headers["Retry-After"]) >= 1
