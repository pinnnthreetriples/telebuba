"""The hand-rolled CDP WebSocket client, against a real loopback server.

No websocket dependency is pulled in for a socket that only ever talks to a browser
we launched ourselves, so the RFC 6455 bits this package does implement — the
handshake, client masking, the 16-bit length form, ping/pong, and routing a reply
past an unsolicited event — are exercised for real rather than mocked.
"""

from __future__ import annotations

import asyncio
import json
import struct
from contextlib import suppress
from typing import Any

import pytest

from core.web_login._cdp import CdpError, CdpSession
from tests.core.test_web_login_browser import attached

_TIMEOUT = 5.0
_HANDSHAKE_END = b"\r\n\r\n"
_SWITCHING = (
    b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
)
_PING_FRAME = bytes([0x89, 0])
_SHORT_LEN_MAX = 125


async def _server_read_client_text(reader: asyncio.StreamReader) -> str:
    """Read one masked client text frame (payloads here are < 126 bytes)."""
    head = await reader.readexactly(2)
    length = head[1] & 0x7F
    mask = await reader.readexactly(4)
    payload = await reader.readexactly(length)
    return bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload)).decode("utf-8")


def _server_text_frame(text: str) -> bytes:
    """One unmasked server text frame, with the 16-bit length form when needed.

    A real CDP event runs well past 125 bytes, so the short form alone would set the
    mask bit in the length byte and desynchronise the client mid-stream.
    """
    payload = text.encode("utf-8")
    if len(payload) <= _SHORT_LEN_MAX:
        return bytes([0x81, len(payload)]) + payload
    return bytes([0x81, 126]) + struct.pack("!H", len(payload)) + payload


@pytest.mark.asyncio
async def test_cdp_session_round_trip_over_loopback() -> None:
    received: dict[str, Any] = {}

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.readuntil(_HANDSHAKE_END)
            writer.write(_SWITCHING)
            await writer.drain()
            command = json.loads(await _server_read_client_text(reader))
            received["command"] = command
            # An unsolicited event first (the pump must not mistake it for a reply),
            # then a server ping to exercise the pong path, then the reply.
            writer.write(_server_text_frame(json.dumps(attached("W1", "worker"))))
            writer.write(_PING_FRAME)
            writer.write(
                _server_text_frame(json.dumps({"id": command["id"], "result": {"ok": True}})),
            )
            await writer.drain()
            await reader.read()  # let the client close (its masked close frame) before we do
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()

    server = await asyncio.start_server(handle, host="127.0.0.1", port=0)
    port = int(server.sockets[0].getsockname()[1])
    try:
        session = await CdpSession.connect(f"ws://127.0.0.1:{port}/devtools/browser/ABC")
        response = await asyncio.wait_for(
            session.send_command(
                "Page.navigate", {"url": "https://web.telegram.org/k/"}, session_id="P1"
            ),
            timeout=_TIMEOUT,
        )
        event = await session.next_target_event(_TIMEOUT)
        await session.aclose()
    finally:
        server.close()
        await server.wait_closed()

    assert received["command"]["method"] == "Page.navigate"
    assert received["command"]["params"] == {"url": "https://web.telegram.org/k/"}
    # The command was addressed to one target, not to the browser itself.
    assert received["command"]["sessionId"] == "P1"
    assert response["result"] == {"ok": True}
    # The event was queued for the driver instead of being taken for a reply.
    assert event is not None
    assert event["params"]["targetInfo"]["type"] == "worker"


def _spy_on_connections(monkeypatch: pytest.MonkeyPatch) -> list[asyncio.StreamWriter]:
    """Capture every socket ``connect`` opens, so a leaked one is visible."""
    opened: list[asyncio.StreamWriter] = []
    real = asyncio.open_connection

    async def _spy(*args: Any, **kwargs: Any) -> Any:
        reader, writer = await real(*args, **kwargs)
        opened.append(writer)
        return reader, writer

    monkeypatch.setattr(asyncio, "open_connection", _spy)
    return opened


async def _connect_against(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
) -> list[asyncio.StreamWriter]:
    opened = _spy_on_connections(monkeypatch)
    server = await asyncio.start_server(handler, host="127.0.0.1", port=0)
    port = int(server.sockets[0].getsockname()[1])
    try:
        with pytest.raises(CdpError):
            await CdpSession.connect(f"ws://127.0.0.1:{port}/devtools/browser/ABC")
    finally:
        server.close()
        await server.wait_closed()
    assert opened, "the spy never saw a connection"
    return opened


@pytest.mark.asyncio
async def test_a_browser_that_closes_mid_handshake_is_a_cdp_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chrome accepts the TCP connection, then closes: shutting down, or port stolen.

    ``readuntil`` raises ``IncompleteReadError``, which subclasses ``EOFError`` and so
    slipped past the launcher's ``(BrowserStartError, CdpError, TimeoutError, OSError)``
    net as a bare 500 with a traceback — defeating the fixed-wording guarantee the
    refusal constants exist for — while leaking one file descriptor per attempt.
    """

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readline()
        writer.close()
        with suppress(OSError):
            await writer.wait_closed()

    opened = await _connect_against(monkeypatch, handle)

    assert all(writer.is_closing() for writer in opened)


@pytest.mark.asyncio
async def test_a_refused_upgrade_closes_the_socket_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused upgrade must not leak the socket the way an unhandled one did.

    What this pins is that the non-101 branch closes the writer at all — the FD leak
    that matters. It does NOT distinguish "closed" from "closed and awaited": a bare
    ``close()`` already flips ``is_closing()``, so the ``wait_closed`` the production
    code also does is not observable from here. Said plainly so the next reader does
    not take this test for more than it proves.
    """

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(_HANDSHAKE_END)
        writer.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
        await writer.drain()
        # The server does NOT wait for the client to hang up: a handler that did would
        # deadlock the teardown against exactly the leak this test is looking for.
        writer.close()
        with suppress(OSError):
            await writer.wait_closed()

    opened = await _connect_against(monkeypatch, handle)

    assert all(writer.is_closing() for writer in opened)
