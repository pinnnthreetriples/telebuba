"""Loopback HTTP-CONNECT relay that fronts an authenticated upstream proxy.

Chrome cannot pass proxy credentials on the command line and cannot do SOCKS5
auth at all, so we bind a tiny credential-free CONNECT proxy on 127.0.0.1 and
forward every tunnel through the operator's authenticated upstream (SOCKS5 or
HTTP CONNECT) via ``python_socks`` — reusing the same dependency and type map
as ``core._proxy_http``. The upstream resolves the destination hostname, giving
socks5h-style no-DNS-leak behavior (``dest_host`` stays a name).

Only the CONNECT method is spoken: Chrome uses CONNECT for HTTPS and
web.telegram.org is HTTPS, so plain-HTTP proxying is out of scope. Credentials
belong to the upstream ``ProxySettings`` and are never logged.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING, Self

from python_socks import ProxyType as SocksProxyType
from python_socks.async_.asyncio import Proxy

if TYPE_CHECKING:
    from schemas.proxy import ProxySettings, ProxyType

_PROXY_TYPE_BY_NAME: dict[ProxyType, SocksProxyType] = {
    "socks5": SocksProxyType.SOCKS5,
    "https": SocksProxyType.HTTP,
}
_DEFAULT_TIMEOUT_SECONDS = 30.0
_PUMP_CHUNK = 64 * 1024
_MAX_PORT = 65_535
_CONNECT_PARTS = 3
_CONNECT_OK = b"HTTP/1.1 200 Connection Established\r\n\r\n"
_BAD_GATEWAY = b"HTTP/1.1 502 Bad Gateway\r\n\r\n"
_BAD_REQUEST = b"HTTP/1.1 400 Bad Request\r\n\r\n"


class LocalProxyRelay:
    """A loopback CONNECT proxy that tunnels through one authenticated upstream.

    Bind with :meth:`start` (returns the OS-assigned port), point Chrome at
    ``127.0.0.1:<port>`` with no credentials, and every ``CONNECT`` is dialed
    through ``upstream`` by ``python_socks``. Usable as an async context manager.
    """

    def __init__(
        self,
        upstream: ProxySettings,
        *,
        connect_timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._upstream = upstream
        self._connect_timeout = connect_timeout
        self._server: asyncio.Server | None = None
        self._conns: set[asyncio.Task[None]] = set()

    async def start(self) -> int:
        """Bind the relay on 127.0.0.1 at a free port and return that port."""
        server = await asyncio.start_server(self._handle_client, host="127.0.0.1", port=0)
        self._server = server
        sockets = server.sockets or ()
        if not sockets:
            msg = "Relay bound no listening socket"
            raise RuntimeError(msg)
        port = sockets[0].getsockname()[1]
        return int(port)

    @property
    def port(self) -> int | None:
        """The bound loopback port while serving, else ``None`` (not started / closed)."""
        server = self._server
        sockets = server.sockets or () if server is not None else ()
        return int(sockets[0].getsockname()[1]) if sockets else None

    async def aclose(self) -> None:
        """Stop accepting, close the listener, and cancel in-flight tunnels."""
        server = self._server
        if server is not None:
            server.close()
            with suppress(OSError):
                await server.wait_closed()
            self._server = None
        conns = list(self._conns)
        for task in conns:
            task.cancel()
        for task in conns:
            with suppress(asyncio.CancelledError):
                await task
        self._conns.clear()

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._conns.add(task)
        try:
            await self._serve(reader, writer)
        finally:
            with suppress(OSError):
                writer.close()
            if task is not None:
                self._conns.discard(task)

    async def _serve(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        target = await self._read_connect_target(reader)
        if target is None:
            await _reply(writer, _BAD_REQUEST)
            return
        host, port = target
        try:
            up_reader, up_writer = await self._dial_upstream(host, port)
        except Exception:  # noqa: BLE001 - python_socks raises mixed types; any dial failure is 502.
            await _reply(writer, _BAD_GATEWAY)
            return
        writer.write(_CONNECT_OK)
        try:
            await writer.drain()
        except OSError:
            up_writer.close()
            return
        await _tunnel(reader, writer, up_reader, up_writer)

    async def _read_connect_target(
        self,
        reader: asyncio.StreamReader,
    ) -> tuple[str, int] | None:
        try:
            header = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"),
                timeout=self._connect_timeout,
            )
        except (OSError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            return None
        request_line = header.split(b"\r\n", 1)[0].decode("latin-1")
        return _parse_connect_target(request_line)

    async def _dial_upstream(
        self,
        host: str,
        port: int,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        proxy = Proxy(
            proxy_type=_PROXY_TYPE_BY_NAME[self._upstream.proxy_type],
            host=self._upstream.host,
            port=self._upstream.port,
            username=self._upstream.username,
            password=self._upstream.password,
        )
        sock = await asyncio.wait_for(
            proxy.connect(dest_host=host, dest_port=port, timeout=self._connect_timeout),
            timeout=self._connect_timeout,
        )
        return await asyncio.open_connection(sock=sock)


def _parse_connect_target(request_line: str) -> tuple[str, int] | None:
    """Parse ``CONNECT host:port HTTP/1.1`` into ``(host, port)`` or ``None``."""
    parts = request_line.split()
    if len(parts) != _CONNECT_PARTS or parts[0].upper() != "CONNECT":
        return None
    host, separator, port_text = parts[1].rpartition(":")
    host = host.strip("[]")
    if not separator or not host or not (port_text.isascii() and port_text.isdigit()):
        return None
    port = int(port_text)
    if not 1 <= port <= _MAX_PORT:
        return None
    return host, port


async def _reply(writer: asyncio.StreamWriter, payload: bytes) -> None:
    with suppress(OSError):
        writer.write(payload)
        await writer.drain()


async def _tunnel(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    up_reader: asyncio.StreamReader,
    up_writer: asyncio.StreamWriter,
) -> None:
    forward = asyncio.create_task(_pump(client_reader, up_writer))
    backward = asyncio.create_task(_pump(up_reader, client_writer))
    try:
        await asyncio.gather(forward, backward)
    finally:
        for task in (forward, backward):
            task.cancel()
        for writer in (up_writer, client_writer):
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(_PUMP_CHUNK)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except OSError:
        return
    finally:
        with suppress(OSError):
            if writer.can_write_eof():
                writer.write_eof()
