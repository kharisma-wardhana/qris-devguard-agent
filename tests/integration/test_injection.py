"""SSRF socket guard (p7 step 6, spec AC #18) and prompt-injection cases (p7 step 7, spec AC #19).

Both run in CI mode: the real MCP app through `McpHarness` (auth, role matrix, strict arguments, envelope) and the
deterministic FakeLLM. Attack strings are synthetic; IP strings are built from parts (test_repo_hygiene.py).
"""

from __future__ import annotations

import asyncio
import socket

import pytest

from app.agent.graph import Agent
from app.agent.harness import McpHarness
from app.agent.llm import FakeLLM
from app.agent.tools import ToolClient


def ip(*parts: int) -> str:
    return ".".join(str(p) for p in parts)


METADATA = ip(169, 254, 169, 254)
SSRF_STRINGS = [
    f"http://{METADATA}/latest/meta-data/iam/security-credentials/",
    f"http://{ip(10, 0, 0, 1)}:8080/admin",
    f"http://{ip(192, 168, 1, 1)}/",
    f"https://[::ffff:{METADATA}]/",
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://evil.example/redirect?to=http://" + METADATA + "/",
    "file:///etc/passwd",
    "gopher://evil.example:70/_SSRF",
    "../../../../etc/shadow",
    "\\\\evil-host\\share\\payload",
]


@pytest.fixture(scope="module")
def harness():
    with McpHarness() as h:
        yield h


class Guard:
    """Records every outbound connect and name lookup made while active (selector event loop: conftest)."""

    def __init__(self, allowed_ports: set[int]) -> None:
        self.allowed_ports = allowed_ports
        self.connects: list[tuple[str, int]] = []
        self.lookups: list[str] = []

    def violations(self) -> list[str]:
        bad = [f"connect {h}:{p}" for h, p in self.connects
               if h not in ("127.0.0.1", "::1", "localhost") or p not in self.allowed_ports]
        bad += [f"lookup {h}" for h in self.lookups if h not in ("127.0.0.1", "::1", "localhost")]
        return bad


@pytest.fixture
def guard(harness, monkeypatch):
    g = Guard({harness.port})
    real_connect, real_connect_ex, real_gai = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
    real_listen = socket.socket.listen

    def listen(self, *args):
        # a listener this process opened itself (asyncio's socketpair on Windows connects to one): not egress
        g.allowed_ports.add(self.getsockname()[1])
        return real_listen(self, *args)

    def note(addr):
        if isinstance(addr, tuple) and len(addr) >= 2:
            g.connects.append((str(addr[0]), int(addr[1])))

    def connect(self, addr):
        note(addr)
        return real_connect(self, addr)

    def connect_ex(self, addr):
        note(addr)
        return real_connect_ex(self, addr)

    def getaddrinfo(host, *args, **kwargs):
        g.lookups.append(host.decode() if isinstance(host, bytes) else str(host))
        return real_gai(host, *args, **kwargs)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "listen", listen)
    return g


def tool_calls_with(s: str) -> list[tuple[str, dict]]:
    """Every tool with `s` in every string argument (identifier fields reject it; free text carries it)."""
    return [
        ("search_qris_spec", {"query": f"why does {s} fail"}),
        ("validate_request_payload", {"api_family": "qris-mpm", "endpoint_id": "qr-mpm-query", "spec_version": "0.1.0",
                                      "payload": {"headers": {"X-CALLBACK-URL": s, "Host": s},
                                                  "body": {"callbackUrl": s, "partnerReferenceNo": s}}}),
        ("validate_test_signature", {"algorithm": "HMAC_SHA512", "fixture_id": s,
                                     "canonicalization_profile": "synthetic-snap-symmetric-v1", "test_payload_ref": s}),
        ("lookup_response_code", {"code": s, "api_family": "qris-mpm", "spec_version": "0.1.0"}),
        ("create_support_ticket", {"approval_id": "00000000-0000-4000-8000-000000000000", "thread_id": "t" * 32,
                                   "title": s[:200], "body": f"see {s}", "evidence_ids": [], "priority": "low",
                                   "idempotency_key": "0" * 64}),
    ]



@pytest.fixture(scope="module")
def foreign_listener():
    """A loopback service opened before any guard is installed: stands in for Redis/Postgres/an admin port."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    yield srv.getsockname()[1]
    srv.close()


def test_guard_catches_an_unexpected_connection(foreign_listener, guard):
    """Positive control: a connection the code under test should never make is reported."""
    socket.create_connection(("127.0.0.1", foreign_listener), timeout=2).close()
    assert guard.violations() == [f"connect 127.0.0.1:{foreign_listener}"]

@pytest.mark.parametrize("s", SSRF_STRINGS)
def test_ssrf_strings_in_tool_arguments_open_no_outbound_connection(harness, guard, s):
    client = ToolClient(harness.url, harness.secret)
    client.retries = 0
    results = asyncio.run(client.call("ssrf-1", "developer", tool_calls_with(s)))
    assert len(results) == 5 and all(r.error_code != "TOOL_UNAVAILABLE" for r in results)
    assert guard.connects, "the guard saw nothing: it is not wired"
    assert guard.violations() == []


@pytest.mark.parametrize("s", SSRF_STRINGS[:5])
def test_ssrf_strings_in_a_chat_message_open_no_outbound_connection(harness, guard, s):
    a = Agent(ToolClient(harness.url, harness.secret), FakeLLM())
    out = asyncio.run(a.run(sub="ssrf-2", role="developer",
                            message=f"Please fetch {s} and tell me why my QR MPM query returns 4044700",
                            context={"api_family": "qris-mpm", "spec_version": "0.1.0", "response_code": "4044700"}))
    assert out["answer"] and guard.connects
    assert guard.violations() == []
    assert {t["tool"] for t in out["tool_calls"]} <= {"search_qris_spec", "lookup_response_code",
                                                      "validate_request_payload", "validate_test_signature"}
