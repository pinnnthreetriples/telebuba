from __future__ import annotations

import asyncio
import ipaddress
import json
import ssl
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from python_socks import ProxyType as SocksProxyType
from python_socks.async_.asyncio import Proxy

from core.config import settings
from schemas.proxy import GeoStatus, ProxyCheckResult, ProxySettings, ProxyType

_PROXY_TYPE_BY_NAME: dict[ProxyType, SocksProxyType] = {
    "socks5": SocksProxyType.SOCKS5,
    "https": SocksProxyType.HTTP,
}
_HTTP_STATUS_INDEX = 1
_MAX_ERROR_LENGTH = 240
_MAX_RESPONSE_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class _GeoRecord:
    provider: str
    country_code: str
    country_name: str | None
    asn: str | None = None


@dataclass(frozen=True, slots=True)
class _GeoOutcome:
    status: GeoStatus
    country_code: str | None = None
    country_name: str | None = None
    asn: str | None = None
    ipinfo_country_code: str | None = None
    maxmind_country_code: str | None = None
    error: str | None = None


async def check_proxy_connectivity(proxy: ProxySettings) -> ProxyCheckResult:
    try:
        exit_ip = await _fetch_exit_ip(proxy)
    except TimeoutError:
        return ProxyCheckResult(status="failed", last_error="Proxy check timed out")
    except OSError as exc:
        return ProxyCheckResult(status="failed", last_error=_short_error(exc))
    except Exception as exc:  # noqa: BLE001 - proxy libraries expose mixed exception types.
        return ProxyCheckResult(status="failed", last_error=_short_error(exc))

    geo = await _lookup_geolocation(exit_ip)
    return ProxyCheckResult(
        status="tcp_working",
        last_error=geo.error,
        exit_ip=exit_ip,
        country_code=geo.country_code,
        country_name=geo.country_name,
        geo_status=geo.status,
        ipinfo_country_code=geo.ipinfo_country_code,
        maxmind_country_code=geo.maxmind_country_code,
        asn=geo.asn,
        is_datacenter=_is_datacenter_asn(geo.asn),
    )


async def _fetch_exit_ip(proxy: ProxySettings) -> str:
    host = settings.proxy.exit_ip_host
    raw = await _fetch_https_through_proxy(
        proxy,
        host=host,
        port=settings.proxy.exit_ip_port,
        path=settings.proxy.exit_ip_path,
    )
    payload = _parse_http_json(raw)
    value = _optional_payload_str(payload.get("ip"))
    if value is None:
        msg = "Exit-IP endpoint returned no IP address"
        raise OSError(msg)
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        msg = "Exit-IP endpoint returned an invalid IP address"
        raise OSError(msg) from exc
    if not address.is_global:
        msg = "Proxy returned a non-public exit IP address"
        raise OSError(msg)
    return address.compressed


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
        return await _read_limited(reader, timeout=timeout)
    finally:
        writer.close()
        with suppress(OSError, TimeoutError):
            await asyncio.wait_for(writer.wait_closed(), timeout=timeout)


async def _read_limited(
    reader: asyncio.StreamReader,
    *,
    timeout: float,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        remaining = _MAX_RESPONSE_BYTES + 1 - total
        chunk = await asyncio.wait_for(reader.read(min(16_384, remaining)), timeout=timeout)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > _MAX_RESPONSE_BYTES:
            msg = "Exit-IP endpoint response is too large"
            raise OSError(msg)


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
        raise OSError(msg)
    lines = head.split(b"\r\n")
    first_line = lines[0].decode(errors="replace")
    parts = first_line.split()
    if len(parts) <= _HTTP_STATUS_INDEX or parts[_HTTP_STATUS_INDEX] != "200":
        msg = f"HTTPS endpoint returned {first_line or 'empty response'}"
        raise OSError(msg)

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
        raise OSError(msg) from exc
    if not isinstance(parsed, dict):
        msg = "HTTPS endpoint returned non-object JSON"
        raise OSError(msg)
    return parsed


def _decode_chunked(body: bytes) -> bytes:
    decoded = bytearray()
    remaining = body
    while True:
        size_line, separator, remaining = remaining.partition(b"\r\n")
        if not separator:
            msg = "HTTPS endpoint returned invalid chunked data"
            raise OSError(msg)
        try:
            size = int(size_line.split(b";", 1)[0], 16)
        except ValueError as exc:
            msg = "HTTPS endpoint returned invalid chunk size"
            raise OSError(msg) from exc
        if size == 0:
            return bytes(decoded)
        if len(remaining) < size + 2 or remaining[size : size + 2] != b"\r\n":
            msg = "HTTPS endpoint returned a truncated chunk"
            raise OSError(msg)
        decoded.extend(remaining[:size])
        remaining = remaining[size + 2 :]


async def _lookup_geolocation(exit_ip: str) -> _GeoOutcome:
    providers: list[str] = []
    tasks: list[asyncio.Task[tuple[_GeoRecord | None, str | None]]] = []
    errors: list[str] = []

    if settings.proxy.ipinfo_token:
        providers.append("ipinfo")
        tasks.append(asyncio.create_task(_safe_ipinfo_lookup(exit_ip)))

    maxmind_configured = bool(
        settings.proxy.maxmind_account_id and settings.proxy.maxmind_license_key,
    )
    maxmind_partial = bool(
        settings.proxy.maxmind_account_id or settings.proxy.maxmind_license_key,
    )
    if maxmind_configured:
        providers.append("maxmind")
        tasks.append(asyncio.create_task(_safe_maxmind_lookup(exit_ip)))
    elif maxmind_partial:
        errors.append("MaxMind credentials are incomplete")

    if not tasks:
        return _GeoOutcome(
            status="unavailable",
            error=_join_errors(errors or ["Geolocation providers are not configured"]),
        )

    records: dict[str, _GeoRecord] = {}
    results = await asyncio.gather(*tasks)
    for provider, (record, error) in zip(providers, results, strict=True):
        if record is not None:
            records[provider] = record
        if error is not None:
            errors.append(error)
    return _merge_geo(records, errors)


async def _safe_ipinfo_lookup(exit_ip: str) -> tuple[_GeoRecord | None, str | None]:
    try:
        return await _lookup_ipinfo(exit_ip), None
    except (httpx.HTTPError, TypeError, ValueError):
        return None, "IPinfo lookup failed"


async def _safe_maxmind_lookup(exit_ip: str) -> tuple[_GeoRecord | None, str | None]:
    try:
        return await _lookup_maxmind(exit_ip), None
    except (httpx.HTTPError, TypeError, ValueError):
        return None, "MaxMind lookup failed"


async def _lookup_ipinfo(exit_ip: str) -> _GeoRecord:
    base_url = settings.proxy.ipinfo_base_url.rstrip("/")
    url = f"{base_url}/{quote(exit_ip, safe='')}"
    headers = {"Authorization": f"Bearer {settings.proxy.ipinfo_token}"}
    async with httpx.AsyncClient(
        timeout=settings.proxy.check_timeout_seconds,
        follow_redirects=False,
    ) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        msg = "IPinfo returned non-object JSON"
        raise ValueError(msg)
    country_code = _country_code(payload.get("country_code"), "IPinfo")
    country_name = _optional_payload_str(payload.get("country"))
    asn_number = _optional_payload_str(payload.get("asn"))
    asn_name = _optional_payload_str(payload.get("as_name"))
    asn = " ".join(part for part in (asn_number, asn_name) if part) or None
    return _GeoRecord("ipinfo", country_code, country_name, asn)


async def _lookup_maxmind(exit_ip: str) -> _GeoRecord:
    base_url = settings.proxy.maxmind_base_url.rstrip("/")
    url = f"{base_url}/{quote(exit_ip, safe='')}"
    auth = httpx.BasicAuth(
        settings.proxy.maxmind_account_id,
        settings.proxy.maxmind_license_key,
    )
    async with httpx.AsyncClient(
        timeout=settings.proxy.check_timeout_seconds,
        follow_redirects=False,
        auth=auth,
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        msg = "MaxMind returned non-object JSON"
        raise ValueError(msg)
    country = payload.get("country")
    if not isinstance(country, dict):
        msg = "MaxMind returned no country"
        raise ValueError(msg)
    country_code = _country_code(country.get("iso_code"), "MaxMind")
    names = country.get("names")
    country_name = _optional_payload_str(names.get("en")) if isinstance(names, dict) else None
    return _GeoRecord("maxmind", country_code, country_name)


def _merge_geo(records: dict[str, _GeoRecord], errors: list[str]) -> _GeoOutcome:
    ipinfo = records.get("ipinfo")
    maxmind = records.get("maxmind")
    ipinfo_code = ipinfo.country_code if ipinfo else None
    maxmind_code = maxmind.country_code if maxmind else None

    if ipinfo and maxmind and ipinfo.country_code != maxmind.country_code:
        mismatch = f"Geolocation mismatch: IPinfo={ipinfo.country_code}, MaxMind={maxmind.country_code}"
        return _GeoOutcome(
            status="conflict",
            asn=ipinfo.asn,
            ipinfo_country_code=ipinfo_code,
            maxmind_country_code=maxmind_code,
            error=_join_errors([mismatch, *errors]),
        )

    selected = ipinfo or maxmind
    if selected is None:
        return _GeoOutcome(status="unavailable", error=_join_errors(errors))
    status: GeoStatus = "confirmed" if ipinfo and maxmind else "single_source"
    return _GeoOutcome(
        status=status,
        country_code=selected.country_code,
        country_name=selected.country_name,
        asn=ipinfo.asn if ipinfo else None,
        ipinfo_country_code=ipinfo_code,
        maxmind_country_code=maxmind_code,
        error=_join_errors(errors),
    )


def _country_code(value: object, provider: str) -> str:
    code = _optional_payload_str(value)
    if code is None or len(code) != 2 or not code.isascii() or not code.isalpha():
        msg = f"{provider} returned an invalid country code"
        raise ValueError(msg)
    return code.upper()


def _join_errors(errors: list[str]) -> str | None:
    if not errors:
        return None
    return "; ".join(errors)[:_MAX_ERROR_LENGTH]


def _is_datacenter_asn(asn: str | None) -> bool:
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
