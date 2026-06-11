from __future__ import annotations

import pytest

from core import proxy_check as proxy_check_module
from core.config import settings
from core.proxy_check import (
    _fetch_check_payload,
    _http_request,
    _parse_http_json,
    _payload_to_result,
    check_proxy_connectivity,
)
from schemas.proxy import AccountProxySettings


class _FakeReader:
    async def read(self) -> bytes:
        return (
            b"HTTP/1.1 200 OK\r\n\r\n"
            b'{"status":"success","query":"45.130.253.155","country":"Netherlands",'
            b'"countryCode":"NL"}'
        )


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


def test_parse_http_json_accepts_success_response() -> None:
    payload = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: application/json\r\n"
        b"\r\n"
        b'{"status":"success","query":"45.130.253.155","country":"Netherlands","countryCode":"NL"}'
    )

    parsed = _parse_http_json(payload)

    assert parsed["query"] == "45.130.253.155"


def test_parse_http_json_rejects_non_200_response() -> None:
    payload = b"HTTP/1.1 429 Too Many Requests\r\n\r\n{}"

    with pytest.raises(OSError, match="429"):
        _parse_http_json(payload)


def test_payload_to_result_maps_geo_fields() -> None:
    result = _payload_to_result(
        {
            "status": "success",
            "query": "45.130.253.155",
            "country": "Netherlands",
            "countryCode": "NL",
        },
    )

    assert result.status == "tcp_working"
    assert result.exit_ip == "45.130.253.155"
    assert result.country_code == "NL"
    assert result.country_name == "Netherlands"


def test_payload_to_result_maps_geo_failure() -> None:
    result = _payload_to_result({"status": "fail", "message": "reserved range"})

    assert result.status == "failed"
    assert result.last_error == "reserved range"


@pytest.mark.asyncio
async def test_check_proxy_connectivity_maps_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(_proxy: AccountProxySettings) -> dict[str, object]:
        raise TimeoutError

    monkeypatch.setattr(proxy_check_module, "_fetch_check_payload", fake_fetch)

    result = await check_proxy_connectivity(
        AccountProxySettings(
            account_id="acc",
            proxy_type="socks5",
            host="127.0.0.1",
            port=9050,
        ),
    )

    assert result.status == "failed"
    assert result.last_error == "Proxy check timed out"


@pytest.mark.asyncio
async def test_check_proxy_connectivity_maps_os_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(_proxy: AccountProxySettings) -> dict[str, object]:
        error_message = "connection refused"
        raise OSError(error_message)

    monkeypatch.setattr(proxy_check_module, "_fetch_check_payload", fake_fetch)

    result = await check_proxy_connectivity(
        AccountProxySettings(
            account_id="acc",
            proxy_type="http",
            host="127.0.0.1",
            port=8080,
        ),
    )

    assert result.status == "failed"
    assert result.last_error == "connection refused"


def test_http_request_normalizes_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.proxy, "check_host", "example.test")
    monkeypatch.setattr(settings.proxy, "check_path", "json")

    request = _http_request()

    assert request.startswith(b"GET /json HTTP/1.1")
    assert b"Host: example.test" in request


@pytest.mark.asyncio
async def test_fetch_check_payload_uses_proxy_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    writer = _FakeWriter()

    class FakeProxy:
        def __init__(self, **kwargs: object) -> None:
            captured["proxy_kwargs"] = kwargs

        async def connect(self, **kwargs: object) -> object:
            captured["connect_kwargs"] = kwargs
            return object()

    async def fake_open_connection(*, sock: object) -> tuple[_FakeReader, _FakeWriter]:
        captured["sock"] = sock
        return _FakeReader(), writer

    monkeypatch.setattr(proxy_check_module, "Proxy", FakeProxy)
    monkeypatch.setattr(proxy_check_module.asyncio, "open_connection", fake_open_connection)

    payload = await _fetch_check_payload(
        AccountProxySettings(
            account_id="acc",
            proxy_type="socks5",
            host="127.0.0.1",
            port=9050,
        ),
    )

    assert payload["query"] == "45.130.253.155"
    assert captured["connect_kwargs"] == {
        "dest_host": settings.proxy.check_host,
        "dest_port": settings.proxy.check_port,
        "timeout": settings.proxy.check_timeout_seconds,
    }
    assert writer.closed is True
