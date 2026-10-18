"""A WebSocket client that drives an ASGI app in-process (no server, no extra package).

httpx's ASGI transport speaks HTTP only, and Starlette's TestClient warns on this
stack (and warnings fail the suite), so the handshake and frames go through the
ASGI messages themselves.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import TracebackType

    from fastapi import FastAPI

_WAIT_SECONDS = 2.0


class Closed(Exception):  # noqa: N818 - mirrors the close frame, not an error
    """The app closed the socket (or refused the handshake) with ``code`` and ``reason``."""

    def __init__(self, code: int, reason: str) -> None:
        super().__init__(code, reason)
        self.code = code
        self.reason = reason


class AsgiWebSocket:
    def __init__(self, app: FastAPI, path: str, headers: Mapping[str, str]) -> None:
        url_path, _, query = path.partition("?")
        self._scope: dict[str, Any] = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "scheme": "ws",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 50000),
            "root_path": "",
            "path": url_path,
            "raw_path": url_path.encode(),
            "query_string": query.encode(),
            "headers": [(b"host", b"testserver")]
            + [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "subprotocols": [],
            "state": {},
        }
        self._app = app
        self._inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._outbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Self:
        self._task = asyncio.create_task(
            self._app(self._scope, self._inbox.get, self._outbox.put)  # ty: ignore[invalid-argument-type]
        )
        await self._inbox.put({"type": "websocket.connect"})
        first = await self._next()
        if first["type"] == "websocket.close":
            raise Closed(first.get("code", 1000), first.get("reason", ""))
        assert first["type"] == "websocket.accept", first
        return self

    async def __aexit__(
        self,
        _type: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        await self._inbox.put({"type": "websocket.disconnect", "code": 1000})
        assert self._task is not None
        async with asyncio.timeout(_WAIT_SECONDS):
            await self._task

    async def _next(self) -> dict[str, Any]:
        async with asyncio.timeout(_WAIT_SECONDS):
            return await self._outbox.get()

    async def receive(self) -> str | bytes:
        message = await self._next()
        if message["type"] == "websocket.close":
            raise Closed(message.get("code", 1000), message.get("reason", ""))
        if message.get("bytes") is not None:
            return message["bytes"]
        return message["text"]

    async def receive_json(self) -> Any:
        text = await self.receive()
        assert isinstance(text, str)
        return json.loads(text)

    async def send_text(self, text: str) -> None:
        await self._inbox.put({"type": "websocket.receive", "text": text})

    async def send_bytes(self, data: bytes) -> None:
        await self._inbox.put({"type": "websocket.receive", "bytes": data})
