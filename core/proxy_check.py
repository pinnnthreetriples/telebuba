from __future__ import annotations

import asyncio
import json
from typing import Any, cast

from python_socks import ProxyType as SocksProxyType
from python_socks.async_.asyncio import Proxy

from core.config import settings
from schemas.proxy import ProxyCheckResult, ProxySettings, ProxyType

_PROXY_TYPE_BY_NAME: dict[ProxyType, SocksProxyType] = {
    "socks5": SocksProxyType.SOCKS5,
    "https": SocksProxyType.HTTP,
}
_HTTP_STATUS_INDEX = 1
_MAX_ERROR_LENGTH = 240


async def check_proxy_connectivity(proxy: ProxySettings) -> ProxyCheckResult:
    try:
        payload = await _fetch_check_payload(proxy)
    except TimeoutError:
        return ProxyCheckResult(status="failed", last_error="Proxy check timed out")
    except OSError as exc:
        return ProxyCheckResult(status="failed", last_error=_short_error(exc))
    except Exception as exc:  # noqa: BLE001 - external proxy libs expose mixed exception types.
        return ProxyCheckResult(status="failed", last_error=_short_error(exc))
    return _payload_to_result(payload)


async def _fetch_check_payload(proxy: ProxySettings) -> dict[str, Any]:
    socks_proxy = Proxy(
        proxy_type=_PROXY_TYPE_BY_NAME[proxy.proxy_type],
        host=proxy.host,
        port=proxy.port,
        username=proxy.username,
        password=proxy.password,
    )
    timeout = settings.proxy.check_timeout_seconds
    sock = await asyncio.wait_for(
        socks_proxy.connect(
            dest_host=settings.proxy.check_host,
            dest_port=settings.proxy.check_port,
            timeout=timeout,
        ),
        timeout=timeout,
    )
    reader, writer = await asyncio.open_connection(sock=sock)
    try:
        request = _http_request()
        writer.write(request)
        await asyncio.wait_for(writer.drain(), timeout=timeout)
        raw = await asyncio.wait_for(reader.read(), timeout=timeout)
    finally:
        writer.close()
        await writer.wait_closed()
    return _parse_http_json(raw)


def _http_request() -> bytes:
    path = settings.proxy.check_path
    if not path.startswith("/"):
        path = f"/{path}"
    return (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {settings.proxy.check_host}\r\n"
        "Accept: application/json\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode()


def _parse_http_json(raw: bytes) -> dict[str, Any]:
    first_line_bytes = raw.split(b"\r\n", 1)[0]
    first_line = first_line_bytes.decode(errors="replace")
    parts = first_line.split()
    if len(parts) <= _HTTP_STATUS_INDEX or parts[_HTTP_STATUS_INDEX] != "200":
        msg = f"Geo endpoint returned {first_line or 'empty response'}"
        raise OSError(msg)
    _, _, body = raw.partition(b"\r\n\r\n")
    parsed = json.loads(body.decode())
    if not isinstance(parsed, dict):
        msg = "Geo endpoint returned non-object JSON"
        raise OSError(msg)
    return cast("dict[str, Any]", parsed)


def _payload_to_result(payload: dict[str, Any]) -> ProxyCheckResult:
    if payload.get("status") != "success":
        message = str(payload.get("message") or "Geo endpoint returned failure")
        return ProxyCheckResult(status="failed", last_error=message[:_MAX_ERROR_LENGTH])
    asn = _optional_payload_str(payload.get("as"))
    return ProxyCheckResult(
        status="tcp_working",
        exit_ip=_optional_payload_str(payload.get("query")),
        country_code=_optional_payload_str(payload.get("countryCode")),
        country_name=_optional_payload_str(payload.get("country")),
        asn=asn,
        is_datacenter=_is_datacenter_asn(asn),
    )


def _is_datacenter_asn(asn: str | None) -> bool:
    """True when the ASN string matches a known hosting/datacenter network."""
    if not asn:
        return False
    lowered = asn.lower()
    return any(keyword in lowered for keyword in settings.proxy.datacenter_asn_keywords)


def _optional_payload_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _short_error(exc: BaseException) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    return text[:_MAX_ERROR_LENGTH]
