from __future__ import annotations

from typing import cast

import httpx
import pytest
import respx

from core import proxy_check as proxy_check_module
from core.config import settings
from core.proxy_check import (
    _decode_chunked,
    _fetch_exit_ip,
    _GeoOutcome,
    _GeoRecord,
    _http_request,
    _lookup_ipinfo,
    _lookup_maxmind,
    _merge_geo,
    _parse_http_json,
    _read_limited,
    check_proxy_connectivity,
)
from schemas.proxy import ProxySettings


class _FakeReader:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    async def read(self, n: int = -1) -> bytes:
        chunk = self.payload[:n]
        self.payload = self.payload[n:]
        return chunk


class _FakeWriter:
    def __init__(self) -> None:
        self.request = b""
        self.closed = False

    def write(self, request: bytes) -> None:
        self.request = request

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def _http_json(payload: bytes) -> bytes:
    return (
        b"HTTP/1.1 200 OK\r\n"
        + f"Content-Length: {len(payload)}\r\n".encode()
        + b"Content-Type: application/json\r\n\r\n"
        + payload
    )


@pytest.mark.asyncio
async def test_read_limited_collects_fragmented_response() -> None:
    reader = _FakeReader(b"first second third")
    assert await _read_limited(reader, timeout_seconds=1) == b"first second third"


def test_parse_http_json_accepts_success_response() -> None:
    parsed = _parse_http_json(_http_json(b'{"ip":"45.130.253.151"}'))
    assert parsed["ip"] == "45.130.253.151"


def test_parse_http_json_rejects_non_200_response() -> None:
    payload = b"HTTP/1.1 429 Too Many Requests\r\n\r\n{}"
    with pytest.raises(OSError, match="429"):
        _parse_http_json(payload)


def test_parse_http_json_decodes_chunked_response() -> None:
    raw = (
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
        b'9\r\n{"ip":"1.\r\n'
        b'7\r\n2.3.4"}\r\n'
        b"0\r\n\r\n"
    )
    assert _parse_http_json(raw)["ip"] == "1.2.3.4"


def test_decode_chunked_rejects_truncated_chunk() -> None:
    with pytest.raises(OSError, match="truncated"):
        _decode_chunked(b"5\r\nabc\r\n")


def test_http_request_normalizes_path() -> None:
    request = _http_request("example.test", "json")
    assert request.startswith(b"GET /json HTTP/1.1")
    assert b"Host: example.test" in request


@pytest.mark.asyncio
async def test_fetch_exit_ip_uses_authenticated_tls_tunnel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    writer = _FakeWriter()
    reader = _FakeReader(_http_json(b'{"ip":"45.130.253.151"}'))

    class FakeProxy:
        def __init__(self, **kwargs: object) -> None:
            captured["proxy_kwargs"] = kwargs

        async def connect(self, **kwargs: object) -> object:
            captured["connect_kwargs"] = kwargs
            return object()

    async def fake_open_connection(**kwargs: object) -> tuple[_FakeReader, _FakeWriter]:
        captured["open_kwargs"] = kwargs
        return reader, writer

    monkeypatch.setattr(proxy_check_module, "Proxy", FakeProxy)
    monkeypatch.setattr(proxy_check_module.asyncio, "open_connection", fake_open_connection)
    monkeypatch.setattr(settings.proxy, "exit_ip_host", "example.test")
    monkeypatch.setattr(settings.proxy, "exit_ip_path", "/ip")
    monkeypatch.setattr(settings.proxy, "exit_ip_port", 443)

    exit_ip = await _fetch_exit_ip(
        ProxySettings(
            proxy_type="socks5",
            host="proxy.example",
            port=1080,
            username="user",
            password="password",
        ),
    )

    assert exit_ip == "45.130.253.151"
    assert captured["connect_kwargs"] == {
        "dest_host": "example.test",
        "dest_port": 443,
        "timeout": settings.proxy.check_timeout_seconds,
    }
    open_kwargs = cast("dict[str, object]", captured["open_kwargs"])
    assert open_kwargs["ssl"] is not None
    assert open_kwargs["server_hostname"] == "example.test"
    assert b"GET /ip HTTP/1.1" in writer.request
    assert writer.closed is True


@pytest.mark.asyncio
async def test_fetch_exit_ip_rejects_private_address(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(*_args: object, **_kwargs: object) -> bytes:
        return _http_json(b'{"ip":"127.0.0.1"}')

    monkeypatch.setattr(proxy_check_module, "_fetch_https_through_proxy", fake_fetch)
    with pytest.raises(OSError, match="non-public"):
        await _fetch_exit_ip(
            ProxySettings(proxy_type="socks5", host="proxy.example", port=1080),
        )


@respx.mock
@pytest.mark.asyncio
async def test_ipinfo_lookup_uses_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.proxy, "ipinfo_token", "test-token")
    monkeypatch.setattr(settings.proxy, "ipinfo_base_url", "https://api.ipinfo.test/lite")
    route = respx.get("https://api.ipinfo.test/lite/45.130.253.151").mock(
        return_value=httpx.Response(
            200,
            json={
                "ip": "45.130.253.151",
                "asn": "AS9009",
                "as_name": "M247 Europe SRL",
                "country_code": "NL",
                "country": "Netherlands",
            },
        ),
    )

    record = await _lookup_ipinfo("45.130.253.151")

    assert record.country_code == "NL"
    assert record.asn == "AS9009 M247 Europe SRL"
    assert route.calls[0].request.headers["Authorization"] == "Bearer test-token"


@respx.mock
@pytest.mark.asyncio
async def test_maxmind_lookup_uses_free_country_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.proxy, "maxmind_account_id", "42")
    monkeypatch.setattr(settings.proxy, "maxmind_license_key", "license")
    monkeypatch.setattr(
        settings.proxy,
        "maxmind_base_url",
        "https://geolite.test/geoip/v2.1/country",
    )
    route = respx.get("https://geolite.test/geoip/v2.1/country/45.130.253.151").mock(
        return_value=httpx.Response(
            200,
            json={
                "country": {
                    "iso_code": "NL",
                    "names": {"en": "Netherlands"},
                },
            },
        ),
    )

    record = await _lookup_maxmind("45.130.253.151")

    assert record.country_code == "NL"
    assert record.country_name == "Netherlands"
    assert route.calls[0].request.headers["Authorization"].startswith("Basic ")


def test_merge_geo_confirms_matching_sources() -> None:
    outcome = _merge_geo(
        {
            "ipinfo": _GeoRecord("ipinfo", "MX", "Mexico", "AS8151 UNINET"),
            "maxmind": _GeoRecord("maxmind", "MX", "Mexico"),
        },
        [],
    )
    assert outcome.status == "confirmed"
    assert outcome.country_code == "MX"
    assert outcome.ipinfo_country_code == "MX"
    assert outcome.maxmind_country_code == "MX"


def test_merge_geo_marks_conflict_without_guessing() -> None:
    outcome = _merge_geo(
        {
            "ipinfo": _GeoRecord("ipinfo", "NL", "Netherlands", "AS9009 M247"),
            "maxmind": _GeoRecord("maxmind", "MX", "Mexico"),
        },
        [],
    )
    assert outcome.status == "conflict"
    assert outcome.country_code is None
    assert outcome.ipinfo_country_code == "NL"
    assert outcome.maxmind_country_code == "MX"
    assert outcome.error == "Geolocation mismatch: IPinfo=NL, MaxMind=MX"


def test_merge_geo_keeps_single_available_source() -> None:
    outcome = _merge_geo(
        {"ipinfo": _GeoRecord("ipinfo", "US", "United States", "AS1 Example")},
        ["MaxMind lookup failed"],
    )
    assert outcome.status == "single_source"
    assert outcome.country_code == "US"
    assert outcome.error == "MaxMind lookup failed"


@pytest.mark.asyncio
async def test_check_proxy_connectivity_maps_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(_proxy: ProxySettings) -> str:
        raise TimeoutError

    monkeypatch.setattr(proxy_check_module, "_fetch_exit_ip", fake_fetch)
    result = await check_proxy_connectivity(
        ProxySettings(proxy_type="socks5", host="127.0.0.1", port=9050),
    )
    assert result.status == "failed"
    assert result.last_error == "Proxy check timed out"


@pytest.mark.asyncio
async def test_geo_failure_does_not_mark_reachable_proxy_dead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch(_proxy: ProxySettings) -> str:
        return "45.130.253.151"

    async def fake_geo(_exit_ip: str) -> _GeoOutcome:
        return _GeoOutcome(status="unavailable", error="IPinfo lookup failed")

    monkeypatch.setattr(proxy_check_module, "_fetch_exit_ip", fake_fetch)
    monkeypatch.setattr(proxy_check_module, "_lookup_geolocation", fake_geo)

    result = await check_proxy_connectivity(
        ProxySettings(proxy_type="socks5", host="proxy.example", port=1080),
    )

    assert result.status == "tcp_working"
    assert result.exit_ip == "45.130.253.151"
    assert result.geo_status == "unavailable"
    assert result.last_error == "IPinfo lookup failed"


def test_datacenter_asn_is_flagged_from_ipinfo_name() -> None:
    assert proxy_check_module._is_datacenter_asn("AS24940 Hetzner Online GmbH") is True
    assert proxy_check_module._is_datacenter_asn("AS8151 UNINET") is False
