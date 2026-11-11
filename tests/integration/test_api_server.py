"""The real API and MCP servers as subprocesses, fake LLM (p5 spec AC #1, #7, #17, #18).

Authentication: no / expired / MCP-audience / foreign-secret tokens get 401 from the API.
Authorization: a kb_maintainer signature question reaches the MCP role matrix and comes back as a
limitation (FORBIDDEN), never a verdict.
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
import yaml

from app.agent import config
from app.agent.harness import free_port
from app.mcp_server import auth
from app.validators import keys

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
def api(tmp_path_factory):
    keys_dir = tmp_path_factory.mktemp("keys")
    wanted = yaml.safe_load((ROOT / "knowledge_base/fixtures/signatures/cases.yaml").read_text(encoding="utf-8"))["keys"]
    keys.generate(keys_dir, wanted)
    mcp_port, api_port = free_port(), free_port()
    env = {**os.environ, "MCP_AUTH_SECRET": SECRET, "DEVGUARD_TEST_KEYS_DIR": str(keys_dir),
           "QDRANT_URL": f"http://127.0.0.1:{free_port()}", "DEVGUARD_TEST_BACKENDS": "1",
           "DEVGUARD_DB_DOTENV": str(keys_dir / "no.env")}           # p7: never the developer's database
    env.pop("DATABASE_URL", None)
    procs = []
    mcp = subprocess.Popen([sys.executable, str(ROOT / "scripts/mcp_server.py"), "--port", str(mcp_port)], cwd=ROOT,
                           env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    procs.append(mcp)
    _wait(mcp, mcp_port, "mcp server")
    api_proc = subprocess.Popen([sys.executable, str(ROOT / "scripts/api_server.py"), "--port", str(api_port),
                                 "--mcp-url", f"http://127.0.0.1:{mcp_port}/mcp", "--fake-llm"], cwd=ROOT, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    procs.append(api_proc)
    _wait(api_proc, api_port, "api server")
    yield f"http://127.0.0.1:{api_port}", api_port
    for p in procs:
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()


SIG = {"message": "Why is my access-token signature rejected?",
       "context": {"api_family": "snap", "signature": {"algorithm": "SHA256withRSA", "fixture_id": "syn-rsa-01",
                                                       "canonicalization_profile": "snap-asymmetric-access-token-v1",
                                                       "test_case_ref": "sig-at-wrong-separator"}}}


def chat(base, tok, body=SIG):
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    return httpx.post(f"{base}/api/v1/chat", json=body, headers=headers, timeout=120)


@pytest.mark.parametrize("name", ["none", "expired", "mcp audience", "other secret"])
def test_authentication(api, name):
    base, _ = api
    tok = {"none": None,
           "expired": auth.mint("d", "developer", secret=SECRET, audience=API_AUD, ttl_minutes=1, now=int(time.time()) - 3600),
           "mcp audience": auth.mint("d", "developer", secret=SECRET),
           "other secret": auth.mint("d", "developer", secret="o" * 48, audience=API_AUD)}[name]
    r = chat(base, tok)
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED" and SECRET not in r.text


def test_developer_gets_a_verdict_backed_answer(api):
    base, _ = api
    r = chat(base, auth.mint("dev-1", "developer", secret=SECRET, audience=API_AUD))
    assert r.status_code == 200, r.text
    vr = r.json()["answer"]["validation_results"][0]
    assert (vr["result"], vr["details"]) == ("INVALID", ["SIG_WRONG_SEPARATOR"])


def test_authorization_kb_maintainer_gets_limitation_not_verdict(api):
    base, _ = api
    r = chat(base, auth.mint("kb-1", "kb_maintainer", secret=SECRET, audience=API_AUD))
    assert r.status_code == 200
    results = [v["result"] for v in r.json()["answer"]["validation_results"]]
    assert results == ["FORBIDDEN"]


def test_health(api):
    base, _ = api
    assert httpx.get(f"{base}/health/live", timeout=10).status_code == 200
    r = httpx.get(f"{base}/health/ready", timeout=10)
    assert r.status_code == 200 and r.json()["checks"] == {"mcp": "up", "llm": "up"}


def test_api_listens_on_loopback_only(api):
    _, port = api
    try:
        external = socket.gethostbyname(socket.gethostname())
    except OSError:
        pytest.skip("no non-loopback address")
    if external.startswith("127."):
        pytest.skip("hostname resolves to loopback")
    with pytest.raises(OSError):
        socket.create_connection((external, port), timeout=1).close()


def test_fake_llm_refused_without_test_flag():
    env = {k: v for k, v in os.environ.items() if k != "DEVGUARD_TEST_BACKENDS"}
    env["MCP_AUTH_SECRET"] = SECRET
    r = subprocess.run([sys.executable, str(ROOT / "scripts/api_server.py"), "--fake-llm", "--port", str(free_port())],
                       cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 1 and "--fake-llm is for tests only" in r.stderr
