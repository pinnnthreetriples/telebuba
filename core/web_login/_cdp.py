"""A minimal client WebSocket + Chrome DevTools Protocol session over loopback.

Just enough of RFC 6455 to drive a Chrome we launched on 127.0.0.1: text frames,
client-masked sends, unmasked server frames, ping/pong and close. No extensions
and no permessage-deflate — none are negotiated for a DevTools socket — so the
package needs no websocket dependency. It speaks only to a browser we started.

The socket is BROWSER-level and multiplexed. ``Target.setAutoAttach`` with
``flatten`` hands every page and worker its own ``sessionId`` on this one
connection, which is the only way to reach a worker's ``navigator`` before its
script runs (page-level overrides do not cross into workers). Commands therefore
carry an optional ``session_id``, and a background pump routes each reply to the
caller waiting on its id.

The pump keeps only ``Target.*`` events. The Page domain is noisy once enabled
and nothing here consumes it, so dropping the rest is what keeps a window that
stays open for hours from growing an unbounded queue.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import struct
from typing import Self
from urllib.parse import urlsplit

_FIN = 0x80
_MASK_FLAG = 0x80
_OP_MASK = 0x0F
_LEN_MASK = 0x7F
_MASK_BYTES = 4
_MAX_7BIT = 125
_LEN_16 = 126
_LEN_64 = 127
_MAX_16BIT = 0xFFFF

_OP_TEXT = 0x1
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA

_DEFAULT_WS_PORT = 80
_SWITCHING = b"101"

# A CDP response for a browser we launched is small; refuse an absurd declared
# length (only reachable on the 64-bit path) before allocating for the read.
_MAX_FRAME_BYTES = 8 * 1024 * 1024
# A command against our own loopback browser answers in milliseconds; this only
# bounds the pathological case so a caller cannot wait on a dead browser forever.
_COMMAND_TIMEOUT = 30.0
_EVENT_PREFIX = "Target."


class CdpError(RuntimeError):
    """The DevTools WebSocket handshake or transport failed."""


class CdpSession:
    """One browser-level CDP connection, multiplexed over target ``sessionId``s."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._id = 0
        self._pending: dict[int, asyncio.Future[dict]] = {}
        self._events: asyncio.Queue[dict] = asyncio.Queue()
        self._write_lock = asyncio.Lock()
        self._pump: asyncio.Task[None] | None = None

    @classmethod
    async def connect(cls, ws_url: str) -> Self:
        """Handshake with ``ws_url`` and start routing replies in the background."""
        parts = urlsplit(ws_url)
        host = parts.hostname or "127.0.0.1"
        port = parts.port or _DEFAULT_WS_PORT
        reader, writer = await asyncio.open_connection(host, port)
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        writer.write(handshake.encode("ascii"))
        await writer.drain()
        status = (await reader.readuntil(b"\r\n\r\n")).split(b"\r\n", 1)[0]
        if _SWITCHING not in status:
            writer.close()
            msg = f"DevTools WebSocket handshake failed: {status!r}"
            raise CdpError(msg)
        session = cls(reader, writer)
        session._pump = asyncio.create_task(session._route())
        return session

    async def send_command(
        self,
        method: str,
        params: dict[str, object] | None = None,
        *,
        session_id: str | None = None,
    ) -> dict:
        """Send one CDP command — to the browser, or to ``session_id`` — and await its reply."""
        self._id += 1
        message_id = self._id
        payload: dict[str, object] = {"id": message_id, "method": method, "params": params or {}}
        if session_id is not None:
            payload["sessionId"] = session_id
        future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        try:
            await self._send_text(json.dumps(payload))
            return await asyncio.wait_for(future, timeout=_COMMAND_TIMEOUT)
        except TimeoutError as exc:
            msg = f"CDP command {method} timed out"
            raise CdpError(msg) from exc
        finally:
            self._pending.pop(message_id, None)

    async def next_target_event(self, wait_seconds: float) -> dict | None:
        """The next queued ``Target.*`` event, or ``None`` when none arrives in time."""
        try:
            return await asyncio.wait_for(self._events.get(), timeout=wait_seconds)
        except TimeoutError:
            return None

    @property
    def closed(self) -> bool:
        """True once the pump has stopped — the browser is gone or we closed the socket."""
        return self._pump is None or self._pump.done()

    async def aclose(self) -> None:
        """Stop routing, send a close frame, and drop the socket."""
        pump = self._pump
        self._pump = None
        if pump is not None:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
        self._fail_pending(CdpError("DevTools session closed"))
        with contextlib.suppress(OSError, CdpError):
            await self._send_control(_OP_CLOSE)
        self._writer.close()
        with contextlib.suppress(OSError):
            await self._writer.wait_closed()

    async def _route(self) -> None:
        """Pump frames forever: replies to their caller, ``Target.*`` events to the queue."""
        try:
            while True:
                message = json.loads(await self._recv_text())
                message_id = message.get("id")
                if message_id is not None:
                    future = self._pending.pop(message_id, None)
                    if future is not None and not future.done():
                        future.set_result(message)
                elif str(message.get("method", "")).startswith(_EVENT_PREFIX):
                    self._events.put_nowait(message)
        except (OSError, CdpError, ValueError, asyncio.IncompleteReadError) as exc:
            self._fail_pending(CdpError(f"DevTools session ended: {exc}"))

    def _fail_pending(self, error: CdpError) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    async def _send_text(self, text: str) -> None:
        payload = text.encode("utf-8")
        header = bytearray([_FIN | _OP_TEXT])
        length = len(payload)
        if length <= _MAX_7BIT:
            header.append(_MASK_FLAG | length)
        elif length <= _MAX_16BIT:
            header.append(_MASK_FLAG | _LEN_16)
            header += struct.pack("!H", length)
        else:
            header.append(_MASK_FLAG | _LEN_64)
            header += struct.pack("!Q", length)
        mask = os.urandom(_MASK_BYTES)
        header += mask
        masked = bytes(byte ^ mask[i % _MASK_BYTES] for i, byte in enumerate(payload))
        # One frame per writer: concurrent sends must not interleave on the wire.
        async with self._write_lock:
            self._writer.write(bytes(header) + masked)
            await self._writer.drain()

    async def _send_control(self, opcode: int, payload: bytes = b"") -> None:
        mask = os.urandom(_MASK_BYTES)
        masked = bytes(byte ^ mask[i % _MASK_BYTES] for i, byte in enumerate(payload))
        async with self._write_lock:
            self._writer.write(bytes([_FIN | opcode, _MASK_FLAG | len(payload)]) + mask + masked)
            await self._writer.drain()

    async def _recv_text(self) -> str:
        while True:
            opcode, payload = await self._recv_frame()
            if opcode == _OP_TEXT:
                return payload.decode("utf-8")
            if opcode == _OP_PING:
                await self._send_control(_OP_PONG, payload)
            elif opcode == _OP_CLOSE:
                msg = "DevTools WebSocket closed by the browser"
                raise CdpError(msg)

    async def _recv_frame(self) -> tuple[int, bytes]:
        head = await self._reader.readexactly(2)
        opcode = head[0] & _OP_MASK
        length = head[1] & _LEN_MASK
        if length == _LEN_16:
            length = struct.unpack("!H", await self._reader.readexactly(2))[0]
        elif length == _LEN_64:
            length = struct.unpack("!Q", await self._reader.readexactly(8))[0]
            if length > _MAX_FRAME_BYTES:
                msg = f"CDP frame length {length} exceeds the {_MAX_FRAME_BYTES}-byte cap"
                raise CdpError(msg)
        mask = await self._reader.readexactly(_MASK_BYTES) if head[1] & _MASK_FLAG else b""
        payload = await self._reader.readexactly(length)
        if mask:
            payload = bytes(byte ^ mask[i % _MASK_BYTES] for i, byte in enumerate(payload))
        return opcode, payload
