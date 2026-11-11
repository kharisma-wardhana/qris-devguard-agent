"""Request-size limit as pure-ASGI middleware (p7 spec Behaviour §3, AC #9).

- A declared `Content-Length` above the limit is refused with HTTP 413 (`PAYLOAD_TOO_LARGE`) before a byte of
  the body is read.
- Otherwise (no header, chunked, or a lying header) the body is read and counted up to the limit; one byte more
  and the request is refused with the same 413. A body within the limit is replayed to the app unchanged.
- The response never contains request bytes. Only methods that carry a body are buffered; GET/streams pass.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.security import send_envelope

BODY_METHODS = {"POST", "PUT", "PATCH"}


class BodyLimit:
    def __init__(self, app: Any, max_bytes: int, on_refuse: Callable[[str], None] | None = None) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.app = app
        self.max_bytes = max_bytes
        self.on_refuse = on_refuse

    async def _refuse(self, send: Any) -> None:
        if self.on_refuse:
            self.on_refuse("PAYLOAD_TOO_LARGE")
        await send_envelope(send, 413, "PAYLOAD_TOO_LARGE", {"Connection": "close"})

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("method") not in BODY_METHODS:
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = None
                if declared is not None and declared > self.max_bytes:
                    await self._refuse(send)
                    return
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":           # client went away before the body finished
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_bytes:
                await self._refuse(send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        replayed = False

        async def replay() -> dict[str, Any]:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
