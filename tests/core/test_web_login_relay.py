"""The loopback CONNECT relay must tunnel bytes through the authenticated upstream.

Chrome points at ``127.0.0.1:<port>`` with no credentials; the relay parses the
``CONNECT`` request, dials the destination through ``python_socks`` (holding the
upstream credentials), and pumps bytes both ways. These tests stand up a real
localhost echo server as the destination and swap ``python_socks.Proxy`` for a
fake whose ``connect`` returns a socket wired to that echo server — so the
CONNECT parsing and the byte pump run for real, without a live upstream proxy.
The fake records ``dest_host`` to prove the hostname is handed to the upstream
(no local DNS resolution / no leak).
"""

from __future__ import annotations

import asyncio
import socket
from contextlib import suppress
from typing import TYPE_CHECKING, Any

import pytest

from core.web_login import LocalProxyRelay
from core.web_login import relay as relay_module
from schemas.proxy import ProxySettings

if TYPE_CHECKING:
    from collections.abc import Callable

_UPSTREAM = ProxySettings(
    proxy_type="socks5",
    host="upstream.example",
    port=1080,
    username="user",
    password="secret",
)
_TIMEOUT = 5.0


async def _start_echo_server() -> tuple[asyncio.AbstractServer, int]:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
        except OSError:
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, host="127.0.0.1", port=0)
    port = int(server.sockets[0].getsockname()[1])
    return server, port


def _echo_proxy_factory(echo_port: int, recorder: dict[str, Any]) -> Callable[..., Any]:
    class _EchoProxy:
        def __init__(self, **kwargs: Any) -> None:
            recorder.setdefault("inits", []).append(kwargs)

        async def connect(
            self,
            *,
            dest_host: str,
            dest_port: int,
            **_kwargs: Any,
        ) -> socket.socket:
            recorder.setdefault("targets", []).append((dest_host, dest_port))
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setblocking(False)  # noqa: FBT003 - stdlib socket API takes a positional bool.
            await asyncio.get_running_loop().sock_connect(sock, ("127.0.0.1", echo_port))
            return sock

    return _EchoProxy


@pytest.mark.asyncio
async def test_connect_tunnels_bytes_through_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    echo_server, echo_port = await _start_echo_server()
    recorder: dict[str, Any] = {}
    monkeypatch.setattr(relay_module, "Proxy", _echo_proxy_factory(echo_port, recorder))
    relay = LocalProxyRelay(_UPSTREAM)
    port = await relay.start()
    assert isinstance(port, int)
    assert port > 0
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
        await writer.drain()
        response = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=_TIMEOUT)
        assert response.startswith(b"HTTP/1.1 200 Connection Established")

        writer.write(b"ping")
        await writer.drain()
        echoed = await asyncio.wait_for(reader.readexactly(4), timeout=_TIMEOUT)
        assert echoed == b"ping"

        writer.close()
        with suppress(OSError):
            await writer.wait_closed()

        # The hostname (not a resolved IP) reaches the upstream: no local DNS leak.
        assert recorder["targets"] == [("example.com", 443)]
    finally:
        await relay.aclose()
        echo_server.close()
        await echo_server.wait_closed()


@pytest.mark.asyncio
async def test_dial_failure_returns_502(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FailingProxy:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def connect(self, **_kwargs: Any) -> socket.socket:
            msg = "upstream refused"
            raise OSError(msg)

    monkeypatch.setattr(relay_module, "Proxy", _FailingProxy)
    relay = LocalProxyRelay(_UPSTREAM)
    port = await relay.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
        await writer.drain()
        response = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=_TIMEOUT)
        assert response.startswith(b"HTTP/1.1 502 Bad Gateway")
        writer.close()
        with suppress(OSError):
            await writer.wait_closed()
    finally:
        await relay.aclose()


@pytest.mark.asyncio
async def test_non_connect_request_gets_400(monkeypatch: pytest.MonkeyPatch) -> None:
    class _NeverDialed:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def connect(self, **_kwargs: Any) -> socket.socket:
            msg = "upstream must not be dialed for a non-CONNECT request"
            raise AssertionError(msg)

    monkeypatch.setattr(relay_module, "Proxy", _NeverDialed)
    relay = LocalProxyRelay(_UPSTREAM)
    port = await relay.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n")
        await writer.drain()
        response = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=_TIMEOUT)
        assert response.startswith(b"HTTP/1.1 400 Bad Request")
        writer.close()
        with suppress(OSError):
            await writer.wait_closed()
    finally:
        await relay.aclose()


@pytest.mark.asyncio
async def test_async_context_manager_stops_listening_on_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    echo_server, echo_port = await _start_echo_server()
    recorder: dict[str, Any] = {}
    monkeypatch.setattr(relay_module, "Proxy", _echo_proxy_factory(echo_port, recorder))
    try:
        async with LocalProxyRelay(_UPSTREAM) as relay:
            server = relay._server
            assert server is not None
            port = int(server.sockets[0].getsockname()[1])
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
            await writer.drain()
            response = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=_TIMEOUT)
            assert response.startswith(b"HTTP/1.1 200 Connection Established")
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()
        # After the context exits the listener is gone, so a new dial is refused.
        with pytest.raises(ConnectionRefusedError):
            await asyncio.open_connection("127.0.0.1", port)
    finally:
        echo_server.close()
        await echo_server.wait_closed()


class _EndedReader:
    """A peer that closed its side: read returns EOF at once."""

    @staticmethod
    async def read(_size: int) -> bytes:
        return b""


class _SilentReader:
    """An upstream that never propagates the half-close: read blocks forever."""

    @staticmethod
    async def read(_size: int) -> bytes:
        await asyncio.sleep(3600)
        return b""


class _NullWriter:
    def __init__(self) -> None:
        self.closed = False

    def write(self, _data: bytes) -> None: ...

    @staticmethod
    async def drain() -> None: ...

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None: ...

    @staticmethod
    def can_write_eof() -> bool:
        return False


@pytest.mark.asyncio
async def test_one_direction_ending_tears_the_whole_tunnel_down() -> None:
    """Waiting for BOTH pumps leaks the tunnel for the life of the backend process.

    An upstream that does not propagate the half-close leaves the surviving pump
    blocked on ``read`` effectively forever — two sockets and a task per tunnel, and
    relays live as long as the process does.
    """
    client_writer, up_writer = _NullWriter(), _NullWriter()

    await asyncio.wait_for(
        relay_module._tunnel(
            _EndedReader(),  # ty: ignore[invalid-argument-type]
            client_writer,  # ty: ignore[invalid-argument-type]
            _SilentReader(),  # ty: ignore[invalid-argument-type]
            up_writer,  # ty: ignore[invalid-argument-type]
        ),
        timeout=_TIMEOUT,
    )

    assert client_writer.closed is True
    assert up_writer.closed is True


class _LateReader:
    """Telegram's side: one more chunk lands just after the client half-closes."""

    def __init__(self) -> None:
        self._sent = False

    async def read(self, _size: int) -> bytes:
        if self._sent:
            await asyncio.sleep(3600)
            return b""
        self._sent = True
        await asyncio.sleep(0.05)
        return b"late"


class _RecordingWriter(_NullWriter):
    def __init__(self) -> None:
        super().__init__()
        self.data = bytearray()

    def write(self, _data: bytes) -> None:
        self.data += _data


@pytest.mark.asyncio
async def test_the_surviving_direction_gets_a_bounded_drain() -> None:
    """A client half-close is not "Telegram has finished answering".

    Cancelling the other pump the instant the first one ends discards whatever is
    still in flight — the tail of a response the page then reads as a network error.
    Bounded, because waiting for it without a limit is exactly the tunnel leak
    FIRST_COMPLETED was introduced to fix.
    """
    client_writer, up_writer = _RecordingWriter(), _NullWriter()

    await asyncio.wait_for(
        relay_module._tunnel(
            _EndedReader(),  # ty: ignore[invalid-argument-type]
            client_writer,  # ty: ignore[invalid-argument-type]
            _LateReader(),  # ty: ignore[invalid-argument-type]
            up_writer,  # ty: ignore[invalid-argument-type]
        ),
        timeout=_TIMEOUT,
    )

    assert bytes(client_writer.data) == b"late"
