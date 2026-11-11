"""Request-size middleware (p7 plan step 1; spec Behaviour §3, AC #9 building block)."""

from __future__ import annotations

import asyncio

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.security import security
from app.security.body_limit import BodyLimit

LIMIT = 64
calls: list[int] = []


async def echo_len(request: Request) -> JSONResponse:
    body = await request.body()
    calls.append(len(body))
    return JSONResponse({"n": len(body)})


def _client(refused: list[str] | None = None) -> TestClient:
    inner = Starlette(routes=[Route("/x", echo_len, methods=["POST", "GET"])])
    return TestClient(BodyLimit(inner, LIMIT, on_refuse=(refused.append if refused is not None else None)))


def setup_function() -> None:
    calls.clear()


def test_small_body_passes_unchanged():
    r = _client().post("/x", content=b"a" * LIMIT)
    assert r.status_code == 200 and r.json() == {"n": LIMIT}


def test_declared_oversize_refused_before_handler():
    refused: list[str] = []
    r = _client(refused).post("/x", content=b"Z" * (LIMIT + 1))
    assert r.status_code == 413
    assert r.json()["code"] == "PAYLOAD_TOO_LARGE" and set(r.json()) == {"code", "message", "correlation_id"}
    assert "ZZ" not in r.text
    assert calls == [] and refused == ["PAYLOAD_TOO_LARGE"]


def test_chunked_oversize_refused_before_handler():
    def gen():
        for _ in range(10):
            yield b"Z" * 16                                    # 160 bytes, no Content-Length
    r = _client().post("/x", content=gen())
    assert r.status_code == 413 and calls == []


def test_lying_content_length_is_still_counted():
    async def run() -> list[dict]:
        sent: list[dict] = []
        inner = Starlette(routes=[Route("/x", echo_len, methods=["POST"])])
        mw = BodyLimit(inner, LIMIT)
        msgs = [{"type": "http.request", "body": b"Z" * 50, "more_body": True},
                {"type": "http.request", "body": b"Z" * 50, "more_body": False}]

        async def receive():
            return msgs.pop(0)

        async def send(m):
            sent.append(m)
        scope = {"type": "http", "method": "POST", "path": "/x", "headers": [(b"content-length", b"10")],
                 "query_string": b"", "root_path": "", "scheme": "http", "server": ("t", 80)}
        await mw(scope, receive, send)
        return sent
    sent = asyncio.run(run())
    assert sent[0]["status"] == 413 and calls == []


def test_get_is_not_buffered():
    assert _client().get("/x").status_code == 200


def test_invalid_limit_rejected():
    with pytest.raises(ValueError):
        BodyLimit(None, 0)


def test_configured_limit_fits_largest_legitimate_request():
    lim = security()["limits"]
    assert lim["max_request_bytes"] >= lim["max_message_chars"] * 4 + lim["max_payload_bytes"]
