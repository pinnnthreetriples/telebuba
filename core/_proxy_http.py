"""Raw HTTPS-over-SOCKS transport — split from ``core.proxy_check`` for the file-size budget.

One request, one response: open a tunnel through the operator's proxy, write a
minimal HTTP/1.1 GET, read a bounded reply, and parse it into a JSON object.
Nothing here knows what the payload means — ``core.proxy_check._fetch_exit_ip``
is the only caller and owns that interpretation. The dependency runs one way
(``proxy_check`` -> here), so this module reaches back into nothing.

``_ProxyCheckError`` lives here because most of its raise sites do, but it spans
both modules: ``core.proxy_check`` re-exports it and ``_short_error`` there
matches on it to decide whose prose may reach the wire. Keep that pair in step.
"""

from __future__ import annotations

import asyncio
import json
import ssl
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Protocol

from python_socks import ProxyType as SocksProxyType
from python_socks.async_.asyncio import Proxy

from core.config import settings

if TYPE_CHECKING:
    from schemas.proxy import ProxySettings, ProxyType

_PROXY_TYPE_BY_NAME: dict[ProxyType, SocksProxyType] = {
    "socks5": SocksProxyType.SOCKS5,
    "https": SocksProxyType.HTTP,
}
_HTTP_STATUS_INDEX = 1
_HTTP_STATUS_CODE_LENGTH = 3
_UNPARSABLE_STATUS_LINE = "HTTPS endpoint returned an unparsable status line"
_MAX_RESPONSE_BYTES = 64 * 1024


class _ProxyCheckError(OSError):
    """A failure the proxy check diagnosed itself, so its message is our own bounded prose.

    An ``OSError`` subclass so the probe's existing ``except OSError`` arm keeps
    catching it, and the marker ``core.proxy_check._short_error`` uses to tell our
    own useful diagnostic apart from third-party text that is not a contract.
    """


class _AsyncReader(Protocol):
    async def read(self, n: int = -1) -> bytes: ...


async def _fetch_https_through_proxy(
    proxy: ProxySettings,
    *,
    host: str,
    port: int,
    path: str,
) -> bytes:
    socks_proxy = Proxy(
        proxy_type=_PROXY_TYPE_BY_NAME[proxy.proxy_type],
        host=proxy.host,
        port=proxy.port,
        username=proxy.username,
        password=proxy.password,
    )
    timeout = settings.proxy.check_timeout_seconds
    sock = await asyncio.wait_for(
        socks_proxy.connect(dest_host=host, dest_port=port, timeout=timeout),
        timeout=timeout,
    )
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(
            sock=sock,
            ssl=ssl.create_default_context(),
            server_hostname=host,
            ssl_handshake_timeout=timeout,
        ),
        timeout=timeout,
    )
    try:
        writer.write(_http_request(host, path))
        await asyncio.wait_for(writer.drain(), timeout=timeout)
        return await _read_limited(reader, timeout_seconds=timeout)
    finally:
        writer.close()
        with suppress(OSError, TimeoutError):
            await asyncio.wait_for(writer.wait_closed(), timeout=timeout)


async def _read_limited(
    reader: _AsyncReader,
    *,
    timeout_seconds: float,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        remaining = _MAX_RESPONSE_BYTES + 1 - total
        chunk = await asyncio.wait_for(
            reader.read(min(16_384, remaining)),
            timeout=timeout_seconds,
        )
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > _MAX_RESPONSE_BYTES:
            msg = "Exit-IP endpoint response is too large"
            raise _ProxyCheckError(msg)


def _http_request(host: str, path: str) -> bytes:
    normalized_path = path if path.startswith("/") else f"/{path}"
    return (
        f"GET {normalized_path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Accept: application/json\r\n"
        "User-Agent: Telebuba/0.1\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode()


def _parse_http_json(raw: bytes) -> dict[str, Any]:
    head, separator, body = raw.partition(b"\r\n\r\n")
    if not separator:
        msg = "HTTPS endpoint returned an incomplete response"
        raise _ProxyCheckError(msg)
    lines = head.split(b"\r\n")
    parts = lines[0].decode(errors="replace").split()
    code = parts[_HTTP_STATUS_INDEX] if len(parts) > _HTTP_STATUS_INDEX else ""
    if code != "200":
        # The CODE is ours to report; the reason phrase after it is remote-controlled
        # text, and this message reaches ``proxy_last_error`` (see ``_failed_result``).
        # ``isascii`` because ``isdigit`` also accepts non-ASCII digits.
        known = len(code) == _HTTP_STATUS_CODE_LENGTH and code.isascii() and code.isdigit()
        msg = f"HTTPS endpoint returned HTTP {code}" if known else _UNPARSABLE_STATUS_LINE
        raise _ProxyCheckError(msg)

    headers: dict[str, str] = {}
    for raw_header in lines[1:]:
        name, delimiter, value = raw_header.partition(b":")
        if delimiter:
            headers[name.decode(errors="replace").strip().lower()] = (
                value.decode(errors="replace").strip().lower()
            )
    if headers.get("transfer-encoding") == "chunked":
        body = _decode_chunked(body)
    try:
        parsed = json.loads(body.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = "HTTPS endpoint returned invalid JSON"
        raise _ProxyCheckError(msg) from exc
    if not isinstance(parsed, dict):
        msg = "HTTPS endpoint returned non-object JSON"
        raise _ProxyCheckError(msg)
    return parsed


def _decode_chunked(body: bytes) -> bytes:
    decoded = bytearray()
    remaining = body
    while True:
        size_line, separator, remaining = remaining.partition(b"\r\n")
        if not separator:
            msg = "HTTPS endpoint returned invalid chunked data"
            raise _ProxyCheckError(msg)
        try:
            size = int(size_line.split(b";", 1)[0], 16)
        except ValueError as exc:
            msg = "HTTPS endpoint returned invalid chunk size"
            raise _ProxyCheckError(msg) from exc
        if size == 0:
            return bytes(decoded)
        if len(remaining) < size + 2 or remaining[size : size + 2] != b"\r\n":
            msg = "HTTPS endpoint returned a truncated chunk"
            raise _ProxyCheckError(msg)
        decoded.extend(remaining[:size])
        remaining = remaining[size + 2 :]
