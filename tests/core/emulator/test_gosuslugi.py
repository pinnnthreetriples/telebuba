"""Госуслуги: its APK found on RuStore, fetched through the phone's proxy, kept on its signer."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import httpx
import pytest
import respx

from core.config import settings
from core.emulator import EmulatorError, _gosuslugi, assets
from schemas.proxy import ProxySettings
from tests.core._apk_builder import build_apk

if TYPE_CHECKING:
    from pathlib import Path

_INFO_URL = "https://backapi.rustore.ru/applicationData/overallInfo/ru.rostel"
_LINK_URL = "https://backapi.rustore.ru/applicationData/v2/download-link"
_APK_URL = "https://static.rustore.ru/2026/9/30/19/gosuslugi.apk"
_CERT = b"gosuslugi's certificate"
_PROXY = ProxySettings(
    proxy_type="socks5", host="10.0.0.9", port=1080, username="u", password="p@ss"
)


def _apk_bytes(tmp_path: Path, certificate: bytes) -> bytes:
    built = tmp_path / "built" / "gosuslugi.apk"
    built.parent.mkdir()
    build_apk(built, certificate=certificate, package="ru.rostel", version=("27.1.0", 9876))
    return built.read_bytes()


@pytest.fixture
def target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "emulator" / "gosuslugi.apk"
    monkeypatch.setattr(settings.emulator, "gosuslugi_apk_path", path)
    monkeypatch.setattr(
        _gosuslugi, "GOSUSLUGI_CERTIFICATE_SHA256", hashlib.sha256(_CERT).hexdigest()
    )
    monkeypatch.setattr(assets, "_LOCKS", {})
    return path


def _rustore(store: respx.MockRouter, apk: bytes) -> respx.Route:
    store.get(_INFO_URL).respond(200, json={"code": "OK", "body": {"appId": 537791}})
    link = store.post(_LINK_URL).respond(
        200, json={"code": "OK", "body": {"downloadUrls": [{"url": _APK_URL}]}}
    )
    store.get(_APK_URL).respond(200, content=apk)
    return link


@pytest.mark.asyncio
async def test_a_missing_apk_is_looked_up_on_rustore_and_kept_on_its_signer(
    target: Path, tmp_path: Path
) -> None:
    with respx.mock(assert_all_called=True) as store:
        link = _rustore(store, _apk_bytes(tmp_path, _CERT))
        assert await _gosuslugi.ensure_gosuslugi_apk(_PROXY) == target

    assert json.loads(link.calls.last.request.content)["appId"] == 537791
    assert link.calls.last.request.headers["ruStoreVerCode"]
    assert target.is_file()
    assert list(target.parent.iterdir()) == [target]


@pytest.mark.asyncio
async def test_an_apk_from_another_signer_is_never_kept(target: Path, tmp_path: Path) -> None:
    with respx.mock() as store:
        _rustore(store, _apk_bytes(tmp_path, b"someone else"))
        with pytest.raises(EmulatorError) as refused:
            await _gosuslugi.ensure_gosuslugi_apk(_PROXY)

    assert refused.value.code == "emulator_signature_mismatch"
    assert list(target.parent.iterdir()) == []


@pytest.mark.asyncio
async def test_an_apk_in_place_asks_rustore_nothing(target: Path) -> None:
    target.parent.mkdir(parents=True)
    target.write_bytes(b"the operator's own")

    with respx.mock(assert_all_called=False) as store:
        info = store.get(_INFO_URL).respond(200, json={})
        await _gosuslugi.ensure_gosuslugi_apk(_PROXY)

    assert not info.called
    assert target.read_bytes() == b"the operator's own"


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(400),
        httpx.Response(200, json={"code": "OK", "body": {"downloadUrls": []}}),
        httpx.Response(200, json={"body": {"downloadUrls": [{"url": "http://plain"}]}}),
        httpx.ConnectError("offline"),
    ],
    ids=["refused", "no-file", "plain-http", "offline"],
)
@pytest.mark.asyncio
async def test_a_lookup_that_names_no_https_file_is_a_failed_download(
    target: Path, answer: httpx.Response | Exception
) -> None:
    with respx.mock() as store:
        store.get(_INFO_URL).respond(200, json={"body": {"appId": 1}})
        store.post(_LINK_URL).mock(side_effect=[answer])
        with pytest.raises(EmulatorError) as refused:
            await _gosuslugi.ensure_gosuslugi_apk(_PROXY)

    assert refused.value.code == "emulator_download_failed"
    assert not target.exists()


def test_the_proxy_url_escapes_its_credentials() -> None:
    assert assets._proxy_url(_PROXY) == "socks5://u:p%40ss@10.0.0.9:1080"
    https = ProxySettings(proxy_type="https", host="h", port=8080)
    assert assets._proxy_url(https) == "http://h:8080"


@pytest.mark.asyncio
async def test_an_installed_app_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    installs: list[object] = []

    async def installed_packages(_serial: str) -> set[str]:
        return {"ru.rostel"}

    async def install_apk(*args: object, **kwargs: object) -> None:
        installs.append((args, kwargs))

    monkeypatch.setattr(_gosuslugi.adb, "installed_packages", installed_packages)
    monkeypatch.setattr(_gosuslugi.adb, "install_apk", install_apk)

    await _gosuslugi.ensure_gosuslugi("127.0.0.1:5555", _PROXY)

    assert installs == []


@pytest.mark.asyncio
async def test_a_missing_app_is_installed_from_the_apk(
    monkeypatch: pytest.MonkeyPatch, target: Path
) -> None:
    installs: list[tuple[str, Path, str | None]] = []
    target.parent.mkdir(parents=True)
    target.write_bytes(b"apk")

    async def installed_packages(_serial: str) -> set[str]:
        return {"org.telegram.messenger.web"}

    async def install_apk(serial: str, apk: Path, *, installer_package: str | None) -> None:
        installs.append((serial, apk, installer_package))

    monkeypatch.setattr(_gosuslugi.adb, "installed_packages", installed_packages)
    monkeypatch.setattr(_gosuslugi.adb, "install_apk", install_apk)

    await _gosuslugi.ensure_gosuslugi("127.0.0.1:5555", _PROXY)

    assert installs == [("127.0.0.1:5555", target, "com.android.packageinstaller")]
