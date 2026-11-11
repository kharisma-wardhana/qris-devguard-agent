"""The real MCP server as a subprocess, reached with the SDK Streamable HTTP client (p4 spec AC #1-#3, #15).

Authentication: no / bad / expired tokens get HTTP 401 before any tool runs.
Authorization: a role outside the security.yaml matrix gets FORBIDDEN.
"""

from __future__ import annotations

import asyncio
import contextlib
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

from app.mcp_server import auth, config
from app.validators import keys

ROOT = Path(__file__).resolve().parents[2]
SECRET = secrets.token_hex(24)
PATH = config.mcp()["server"]["path"]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    keys_dir = tmp_path_factory.mktemp("keys")
    import yaml
    wanted = yaml.safe_load((ROOT / "knowledge_base/fixtures/signatures/cases.yaml").read_text(encoding="utf-8"))["keys"]
    keys.generate(keys_dir, wanted)
    port = _free_port()
    env = {**os.environ, "MCP_AUTH_SECRET": SECRET, "DEVGUARD_TEST_KEYS_DIR": str(keys_dir),
           "QDRANT_URL": f"http://127.0.0.1:{_free_port()}",         # nothing listens there: search unavailable
           "DEVGUARD_DB_DOTENV": str(keys_dir / "no.env")}           # p7: never the developer's database
    env.pop("DATABASE_URL", None)
    proc = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "mcp_server.py"), "--port", str(port)], cwd=ROOT,
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    deadline = time.time() + 60
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail("server exited: " + proc.stderr.read())
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("server did not start")
    yield f"http://127.0.0.1:{port}{PATH}", port, proc
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.mark.parametrize("name,token", [
    ("none", None),
    ("garbage", "not-a-token"),
    ("other secret", auth.mint("dev-1", "developer", secret="o" * 48)),
    ("expired", auth.mint("dev-1", "developer", secret=SECRET, ttl_minutes=1, now=int(time.time()) - 3600)),
])
def test_unauthenticated_requests_get_401(server, name, token):
    url, _, _ = server
    headers = dict(HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = httpx.post(url, headers=headers, json=INIT, timeout=10)
    assert r.status_code == 401, (name, r.status_code)
    assert "Traceback" not in r.text and SECRET not in r.text


def test_foreign_host_header_is_refused(server):
    url, port, _ = server
    headers = {**HEADERS, "Authorization": f"Bearer {auth.mint('dev-1', 'developer', secret=SECRET)}",
               "Host": f"evil.example:{port}"}
    r = httpx.post(url, headers=headers, json=INIT, timeout=10)
    assert r.status_code in (400, 403, 421)


async def _session(url: str, role: str, calls: list[tuple[str, dict]]):
    import httpx2
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    token = auth.mint("it-user", role, secret=SECRET)
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30) as hc:
        async with streamable_http_client(url, http_client=hc) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                tools = await session.list_tools()
                results = []
                for name, args in calls:
                    res = await session.call_tool(name, args)
                    results.append((res.is_error, json.loads(res.content[0].text)))
                return [t.name for t in tools.tools], results


CALLS = [
    ("validate_request_payload", {"api_family": "qris-mpm", "endpoint_id": "qr-mpm-generate", "spec_version": "0.1.0",
                                  "payload": {"headers": {"Content-Type": "application/json"}, "body": {}}}),
    ("validate_test_signature", {"algorithm": "HMAC_SHA512", "fixture_id": "syn-hmac-01",
                                 "canonicalization_profile": "synthetic-snap-symmetric-v1",
                                 "test_payload_ref": "sig-gen-body-not-minified"}),
    ("lookup_response_code", {"code": "4094700", "api_family": "qris-mpm", "spec_version": "0.1.0"}),
    ("search_qris_spec", {"query": "signature invalid"}),
]


def test_developer_lists_four_tools_and_calls_each_validator(server):
    url, _, _ = server
    names, results = asyncio.run(_session(url, "developer", CALLS))
    assert sorted(names) == sorted(config.TOOL_NAMES)
    (e1, payload), (e2, sig), (e3, code), (e4, search) = results
    assert not e1 and payload["valid"] is False and payload["schema_version"] == "0.1.0"
    assert not e2 and (sig["status"], sig["diagnostic_code"]) == ("INVALID", "SIG_BODY_NOT_MINIFIED")
    assert not e3 and code["status"] == "FOUND" and code["synthetic"] is True
    assert e4 and search["code"] == "SEARCH_UNAVAILABLE"                   # no Qdrant in this test


def test_kb_maintainer_is_forbidden_from_signature_tool(server):
    url, _, _ = server
    _, results = asyncio.run(_session(url, "kb_maintainer", [CALLS[1], CALLS[2]]))
    (e_sig, sig), (e_code, code) = results
    assert e_sig and sig["code"] == "FORBIDDEN" and set(sig) == {"code", "message", "correlation_id"}
    assert not e_code and code["status"] == "FOUND"


def test_server_listens_on_loopback_only(server):
    _, port, _ = server
    try:
        external = socket.gethostbyname(socket.gethostname())
    except OSError:
        pytest.skip("no non-loopback address on this machine")
    if external.startswith("127."):
        pytest.skip("hostname resolves to loopback")
    with pytest.raises(OSError):
        socket.create_connection((external, port), timeout=1).close()


def test_mint_dev_token_cli(tmp_path):
    env = {**os.environ, "MCP_AUTH_SECRET": SECRET}
    run = lambda *a: subprocess.run([sys.executable, str(ROOT / "scripts" / "mint_dev_token.py"), *a], cwd=ROOT,  # noqa: E731
                                    env=env, capture_output=True, text=True, encoding="utf-8")
    ok = run("--sub", "dev-1", "--role", "developer")
    assert ok.returncode == 0 and ok.stdout.count(".") == 2 and SECRET not in ok.stdout + ok.stderr
    claims = auth.HS256Verifier(SECRET).claims(ok.stdout.strip())
    assert claims["role"] == "developer" and claims["sub"] == "dev-1"
    assert run("--sub", "dev-1", "--role", "root").returncode != 0
    assert run("--sub", "dev-1", "--role", "developer", "--ttl", "61").returncode == 1


def test_server_refuses_to_start_without_secret(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "MCP_AUTH_SECRET"}
    env["MCP_AUTH_SECRET"] = "Xq7tooFewChars"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "mcp_server.py"), "--port", str(_free_port())],
                       cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 1 and "MCP_AUTH_SECRET" in r.stderr and "Xq7tooFewChars" not in r.stderr


def test_mint_dev_token_audience_option(server):
    """p5 regression: no --audience still mints an MCP token; --audience api mints one the MCP server rejects."""
    url, _, _ = server
    env = {**os.environ, "MCP_AUTH_SECRET": SECRET}

    def mint_cli(*extra):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "mint_dev_token.py"), "--sub", "dev-1", "--role",
                            "developer", *extra], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
        assert r.returncode == 0, r.stderr
        return r.stdout.strip()

    assert auth.HS256Verifier(SECRET).claims(mint_cli()) is not None
    assert auth.HS256Verifier(SECRET).claims(mint_cli("--audience", "mcp")) is not None
    api_token = mint_cli("--audience", "api")
    r = httpx.post(url, headers={**HEADERS, "Authorization": f"Bearer {api_token}"}, json=INIT, timeout=10)
    assert r.status_code == 401



# --- p7 step 4: start-up refusals (spec AC #12, #16) ----------------------------------------------------

def _ip(*parts):
    return ".".join(str(p) for p in parts)


def _start_once(env_over, tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("DEVGUARD_") or k == "DEVGUARD_TEST_KEYS_DIR"}
    env.pop("DATABASE_URL", None)
    env.update({"MCP_AUTH_SECRET": SECRET, "DEVGUARD_DB_DOTENV": str(tmp_path / "no.env"), **env_over})
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "mcp_server.py"), "--port", str(_free_port())],
                          cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)


def test_server_with_database_but_no_role_password_refuses_to_start(tmp_path):
    owner = f"postgresql://devguard:SYNowner77@{_ip(127, 0, 0, 1)}:1/devguard"
    r = _start_once({"DATABASE_URL": owner}, tmp_path)
    assert r.returncode == 1
    assert "DEVGUARD_" in r.stderr and "_DB_PASSWORD" in r.stderr and "SYNowner77" not in r.stderr + r.stdout
    assert "Traceback" not in r.stderr


def test_server_refuses_a_disallowed_destination(tmp_path):
    r = _start_once({"QDRANT_URL": f"http://{_ip(169, 254, 169, 254)}:6333"}, tmp_path)
    assert r.returncode == 1 and "QDRANT_URL" in r.stderr and _ip(169, 254, 169, 254) not in r.stderr



# --- p7 step 5: limits and malformed input against the real subprocess (spec AC #8, #10) ----------------

MARK = "ECHOMARK" + "77"


def _raw(url, body, token=None):
    headers = dict(HEADERS)
    headers["Authorization"] = f"Bearer {token or auth.mint('raw-1', 'developer', secret=SECRET)}"
    return httpx.post(url, headers=headers, content=body, timeout=10)


@pytest.mark.parametrize("name,body", [
    ("malformed json", b'{"jsonrpc": "2.0", "id": 1, "method": "tools/call", ' + MARK.encode()),
    ("array body", b'[1, 2, "' + MARK.encode() + b'"]'),
    ("string body", b'"' + MARK.encode() + b'"'),
    ("unknown method", b'{"jsonrpc":"2.0","id":1,"method":"' + MARK.encode() + b'","params":{}}'),
])
def test_malformed_jsonrpc_gets_a_fixed_message_without_echo(server, name, body):
    url, _, _ = server
    r = _raw(url, body)
    assert r.status_code in (200, 400), name
    err = r.json()["error"]
    assert set(err) == {"code", "message"} and MARK not in r.text and "Traceback" not in r.text


@pytest.mark.parametrize("name,arguments,tool", [
    ("unknown tool", {}, MARK),
    ("extra argument", {"code": "4094700", "api_family": "qris-mpm", "spec_version": "0.1.0", "x": MARK},
     "lookup_response_code"),
    ("wrong type", {"code": [MARK], "api_family": "qris-mpm", "spec_version": "0.1.0"}, "lookup_response_code"),
])
def test_bad_tool_calls_get_the_envelope_without_echo(server, name, arguments, tool):
    url, _, _ = server
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": arguments}}).encode()
    r = _raw(url, body)
    result = r.json()["result"]
    env = json.loads(result["content"][0]["text"])
    assert result["isError"] and env["code"] == "INVALID_ARGUMENT" and MARK not in r.text, name


def test_oversized_body_gets_413_envelope(server):
    url, _, _ = server
    r = _raw(url, b'{"x":"' + b"Z" * (config.mcp()["server"]["max_body_bytes"] + 10) + b'"}')
    assert r.status_code == 413 and r.json()["code"] == "PAYLOAD_TOO_LARGE" and "ZZZZ" not in r.text


@contextlib.contextmanager
def _dedicated_server(tmp_path):
    """A fresh DB-less server: the per-address bucket is shared by every loopback caller, so burst tests must
    not share a server with the rest of the module (p7 step 5 finding)."""
    port = _free_port()
    env = {**os.environ, "MCP_AUTH_SECRET": SECRET, "DEVGUARD_DB_DOTENV": str(tmp_path / "no.env")}
    env.pop("DATABASE_URL", None)
    log = (tmp_path / "mcp.log").open("w", encoding="utf-8")       # not PIPE: an unread pipe fills (~4 KB on
    proc = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "mcp_server.py"), "--port", str(port)], cwd=ROOT,
                            env=env, stdout=log, stderr=log)                # Windows) and blocks the server's logging
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.2)
        yield f"http://127.0.0.1:{port}{PATH}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


def test_per_subject_tool_burst_is_throttled(tmp_path):
    from app.agent.tools import ToolClient
    from app.security import security
    burst = security()["limits"]["rate"]["mcp_tool_call"]["burst"]
    call = ("lookup_response_code", {"code": "4094700", "api_family": "qris-mpm", "spec_version": "0.1.0"})

    async def go(url, sub, n):
        client = ToolClient(url, SECRET)
        client.retries = 0
        return await client.call(sub, "developer", [call] * n)
    with _dedicated_server(tmp_path) as url:
        results = asyncio.run(go(url, "burst-sub-1", burst + 3))
        codes = [r.data.get("code") if not r.ok else "OK" for r in results]
        assert codes[:burst - 2].count("OK") == burst - 2           # refill may add one or two, never fewer
        assert "RATE_LIMITED" in codes and "TOOL_UNAVAILABLE" not in codes
        limited = next(r.data for r in results if not r.ok)
        assert set(limited) == {"code", "message", "correlation_id"}
        (other,) = asyncio.run(go(url, "burst-sub-2", 1))           # a different subject is unaffected
        assert other.ok


def test_per_address_flood_is_throttled_before_auth(tmp_path):
    from app.security import security
    n = security()["limits"]["rate"]["per_address"]["burst"] + 60
    with _dedicated_server(tmp_path) as url:
        with httpx.Client(timeout=10) as c:
            statuses = [c.post(url, headers=HEADERS, json=INIT).status_code for _ in range(n)]
            assert statuses[0] == 401 and 429 in statuses
            r = c.post(url, headers=HEADERS, json=INIT)
        assert r.status_code == 429 and r.json()["code"] == "RATE_LIMITED" and int(r.headers["retry-after"]) >= 1
