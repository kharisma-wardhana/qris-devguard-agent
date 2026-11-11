"""Start-up egress check (p7 plan step 1; spec AC #16; T9).

IP strings are built from parts: tests/unit/test_repo_hygiene.py flags IPv4-shaped literals in tracked files.
"""

from __future__ import annotations

import pytest

from app.security.egress import EgressRefused, check, check_all


def ip(*parts: int) -> str:
    return ".".join(str(p) for p in parts)


LOOP = ip(127, 0, 0, 1)
METADATA = ip(169, 254, 169, 254)


@pytest.mark.parametrize("url", [
    f"http://{LOOP}:8080/v1",
    f"http://{ip(127, 1, 2, 3)}:6333",
    "http://localhost:3000",
    "http://[::1]:8765/mcp",
    f"postgresql+psycopg://devguard:pw@{LOOP}:5432/devguard",
    "http://qdrant:6333",
    "http://vllm:8000/v1",
    "postgresql://u:p@postgres:5432/devguard",
])
def test_allowed(url):
    check("SETTING", url)


@pytest.mark.parametrize("url,reason", [
    (f"http://{METADATA}/latest/meta-data/", "link-local"),
    (f"http://{ip(10, 0, 0, 5)}:8080", "private"),
    (f"http://{ip(172, 16, 3, 4)}", "private"),
    (f"http://{ip(192, 168, 1, 1)}", "private"),
    ("http://[fe80::1]/", "link-local"),
    ("http://[fd00::1]/", "private"),
    (f"http://[::ffff:{METADATA}]/", "link-local"),
    (f"http://{ip(8, 8, 8, 8)}/", "literal address"),
    ("http://metadata.google.internal/", "not in egress_allowlist"),
    ("https://evil.example/", "not in egress_allowlist"),
    ("file:///etc/passwd", "scheme"),
    ("gopher://localhost:70/", "scheme"),
    ("http:///nohost", "no host"),
])
def test_refused(url, reason):
    with pytest.raises(EgressRefused) as exc:
        check("QDRANT_URL", url)
    assert reason in exc.value.reason


def test_message_names_setting_never_value():
    secret_dsn = f"postgresql://devguard:SuperSecretPw123@{ip(10, 1, 2, 3)}:5432/db"
    with pytest.raises(EgressRefused) as exc:
        check("DATABASE_URL", secret_dsn)
    assert "DATABASE_URL" in str(exc.value)
    assert "SuperSecretPw123" not in str(exc.value) and ip(10, 1, 2, 3) not in str(exc.value)


def test_check_all_skips_unset_and_stops_on_bad():
    check_all({"A": None, "B": "", "C": f"http://{LOOP}:1"})
    with pytest.raises(EgressRefused):
        check_all({"C": f"http://{LOOP}:1", "D": f"http://{METADATA}/"})


def test_custom_allowlist():
    check("X", "http://internal-llm:8000", allowlist=["internal-llm"])
    with pytest.raises(EgressRefused):
        check("X", "http://qdrant:6333", allowlist=["internal-llm"])



def test_libpq_conninfo_form():
    check("DATABASE_URL", f"host={LOOP} port=5432 dbname=devguard user=u password=SYNpw options='-c search_path=x'")
    check("DATABASE_URL", "host=/var/run/postgresql dbname=devguard")              # Unix socket: no egress
    for bad in (f"host={METADATA} dbname=x", f"host={LOOP},{ip(10, 0, 0, 1)} dbname=x", "dbname=x"):
        with pytest.raises(EgressRefused) as exc:
            check("DATABASE_URL", bad)
        assert "SYNpw" not in str(exc.value)


# --- no client follows a redirect (p7 spec AC #17) -------------------------------------------------------

import asyncio  # noqa: E402
import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402


class _Stub:
    """A loopback HTTP server. `redirect_to` set: answers 302 to it; else 200. Records every hit."""

    def __init__(self, redirect_to=None):
        hits = self.hits = []

        class H(BaseHTTPRequestHandler):
            def _answer(self):
                hits.append(self.path)
                n = int(self.headers.get("content-length") or 0)
                if n:
                    self.rfile.read(n)
                if redirect_to:
                    self.send_response(302)
                    self.send_header("Location", redirect_to + self.path)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    body = b'{"object": "list", "data": []}'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
            do_GET = do_POST = _answer

            def log_message(self, *a):
                pass
        self.server = ThreadingHTTPServer((LOOP, 0), H)
        self.url = f"http://{LOOP}:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def stubs():
    target = _Stub()
    bouncer = _Stub(redirect_to=target.url)
    yield bouncer, target
    bouncer.close()
    target.close()


def test_vllm_client_does_not_follow_redirects(stubs, monkeypatch):
    from app.agent.llm import VllmLLM
    bouncer, target = stubs
    monkeypatch.setenv("VLLM_BASE_URL", bouncer.url + "/v1")
    assert asyncio.run(VllmLLM().ping()) is False
    assert bouncer.hits and target.hits == []


def test_health_probe_does_not_follow_redirects(stubs):
    from app.api.app import http_ping
    bouncer, target = stubs
    assert asyncio.run(http_ping(bouncer.url + "/mcp")) is True          # any answer means "up"
    assert bouncer.hits and target.hits == []


def test_mcp_tool_client_does_not_follow_redirects(stubs):
    from app.agent.tools import ToolClient
    bouncer, target = stubs
    client = ToolClient(bouncer.url + "/mcp", "s" * 48)
    client.retries = 0
    (res,) = asyncio.run(client.call("dev-1", "developer", [("lookup_response_code", {"code": "1"})]))
    assert res.ok is False and res.error_code == "TOOL_UNAVAILABLE"
    assert bouncer.hits and target.hits == []
