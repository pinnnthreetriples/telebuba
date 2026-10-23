"""The phone's own Telegram is driven; a phone without one gets the host's APK.

A phone set up from an older APK (``org.telegram.messenger``) keeps that app and its
sign-in even after the host's APK became telegram.org's ``org.telegram.messenger.web``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core import android_apk
from core.android_apk import AppIdentity
from core.config import settings
from core.emulator import redroid, telegram_app

if TYPE_CHECKING:
    from tests.core.emulator.conftest import Console

_SERIAL = "127.0.0.1:5600"
_STORE = "org.telegram.messenger"
_WEB = "org.telegram.messenger.web"


@pytest.fixture(autouse=True)
def host_apk(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The host holds telegram.org's APK; fetching it is recorded, never downloaded."""
    fetched: list[str] = []
    identity = AppIdentity(_WEB, "13.0.3 (71839)")

    async def ensure() -> AppIdentity:
        fetched.append("apk")
        return identity

    monkeypatch.setattr(settings.emulator, "telegram_package", _STORE)
    monkeypatch.setattr(telegram_app, "local_apk_identity", lambda: identity)
    monkeypatch.setattr(telegram_app, "ensure_telegram_apk", ensure)
    monkeypatch.setattr(android_apk, "local_apk_identity", lambda: identity)
    return fetched


def _phone(console: Console, *packages: str) -> None:
    listing = "".join(f"package:{package}\n" for package in ("com.android.shell", *packages))
    console.answer = lambda argv: listing if argv[-2:] == ["list", "packages"] else ""


def _started(console: Console) -> list[str]:
    return [call[call.index("-p") + 1] for call in console.shell_calls() if call[0] == "monkey"]


def _installs(console: Console) -> list[list[str]]:
    return [call for call in console.calls if "install" in call]


@pytest.mark.asyncio
async def test_a_phone_with_the_older_app_keeps_it(console: Console, host_apk: list[str]) -> None:
    _phone(console, _STORE, _WEB)

    await telegram_app.ensure_app(_SERIAL)

    assert _started(console) == [_STORE], "the configured package wins where both are"
    assert _installs(console) == []
    assert host_apk == []


@pytest.mark.asyncio
async def test_a_phone_with_the_web_build_uses_it(console: Console) -> None:
    _phone(console, _WEB)

    await telegram_app.bring_app_to_front(_SERIAL)

    assert _started(console) == [_WEB]


@pytest.mark.asyncio
async def test_a_phone_without_telegram_gets_the_hosts_apk(
    console: Console, host_apk: list[str], tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    apk = tmp_path / "telegram.apk"
    apk.write_bytes(b"apk")
    monkeypatch.setattr(settings.emulator, "telegram_apk_path", apk)
    _phone(console)

    await telegram_app.ensure_app(_SERIAL)

    assert host_apk == ["apk"], "fetched (or read) before the install"
    assert [call[-1] for call in _installs(console)] == [str(apk)]
    assert _installs(console)[0][3:] == [
        "install",
        "-r",
        "-i",
        "com.android.packageinstaller",
        str(apk),
    ]
    assert _started(console) == [_WEB]


@pytest.mark.asyncio
async def test_ensure_app_installs_play_store_apk_with_vending_installer(
    console: Console, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    apk = tmp_path / "telegram.apk"
    apk.write_bytes(b"apk")
    store_identity = AppIdentity(_STORE, "11.4.2 (5555)")

    async def ensure_store() -> AppIdentity:
        return store_identity

    monkeypatch.setattr(telegram_app, "local_apk_identity", lambda: store_identity)
    monkeypatch.setattr(telegram_app, "find_store_apk", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(telegram_app, "ensure_telegram_apk", ensure_store)
    monkeypatch.setattr(settings.emulator, "telegram_apk_path", apk)
    _phone(console)

    await telegram_app.ensure_app(_SERIAL)

    assert _installs(console)[0][3:] == [
        "install",
        "-r",
        "-i",
        "com.android.vending",
        str(apk),
    ]
    assert _started(console) == [_STORE]


@pytest.mark.asyncio
async def test_ensure_app_installs_explicit_apk_path_with_packageinstaller(
    console: Console, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    apk = tmp_path / "web.apk"
    apk.write_bytes(b"web-apk")
    web_identity = AppIdentity(_WEB, "13.0.3 (71839)")

    monkeypatch.setattr(telegram_app, "app_identity", lambda _p: web_identity)
    _phone(console)

    await telegram_app.ensure_app(_SERIAL, apk_path=apk)

    assert _installs(console)[0][3:] == [
        "install",
        "-r",
        "-i",
        "com.android.packageinstaller",
        str(apk),
    ]
    assert _started(console) == [_WEB]


@pytest.mark.asyncio
async def test_ensure_app_installs_explicit_apk_path_with_vending(
    console: Console, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    apk = tmp_path / "store.apk"
    apk.write_bytes(b"store-apk")
    store_identity = AppIdentity(_STORE, "11.4.2 (5555)")

    monkeypatch.setattr(telegram_app, "app_identity", lambda _p: store_identity)
    _phone(console)

    await telegram_app.ensure_app(_SERIAL, apk_path=apk)

    assert _installs(console)[0][3:] == [
        "install",
        "-r",
        "-i",
        "com.android.vending",
        str(apk),
    ]
    assert _started(console) == [_STORE]


@pytest.mark.asyncio
async def test_ensure_app_installs_catalog_found_web_apk_with_packageinstaller(
    console: Console, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    apk = tmp_path / "store_catalog.apk"
    apk.write_bytes(b"catalog-apk")
    web_identity = AppIdentity(_WEB, "13.0.3 (71839)")

    monkeypatch.setattr(telegram_app, "find_store_apk", lambda *_args, **_kwargs: apk)
    monkeypatch.setattr(telegram_app, "app_identity", lambda _p: web_identity)
    _phone(console)

    await telegram_app.ensure_app(_SERIAL)

    assert _installs(console)[0][3:] == [
        "install",
        "-r",
        "-i",
        "com.android.packageinstaller",
        str(apk),
    ]
    assert _started(console) == [_WEB]


def _listings(console: Console) -> int:
    return sum(call[-2:] == ["list", "packages"] for call in console.calls)


@pytest.mark.asyncio
async def test_the_phones_telegram_is_read_once_per_container_run(console: Console) -> None:
    _phone(console, _WEB)

    await telegram_app.bring_app_to_front(_SERIAL)
    await telegram_app.bring_app_to_front(_SERIAL)

    assert _listings(console) == 1
    assert _started(console) == [_WEB, _WEB]
    await redroid.launch_instance("telebuba-acc", 0)  # _SERIAL's index: maybe another phone now
    await telegram_app.bring_app_to_front(_SERIAL)
    assert _listings(console) == 2, "a started container's apps are read again"


@pytest.mark.asyncio
async def test_a_phone_given_telegram_is_read_again(
    console: Console, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    apk = tmp_path / "telegram.apk"
    apk.write_bytes(b"apk")
    monkeypatch.setattr(settings.emulator, "telegram_apk_path", apk)
    _phone(console)
    await telegram_app.ensure_app(_SERIAL)
    _phone(console, _WEB)

    await telegram_app.bring_app_to_front(_SERIAL)

    assert _listings(console) == 2, "no Telegram is never remembered: the install is seen"
    assert _started(console) == [_WEB, _WEB]


@pytest.mark.asyncio
async def test_a_lookup_overtaken_by_a_new_container_is_not_remembered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listed = asyncio.Event()
    release = asyncio.Event()

    async def old_phone(_serial: str) -> set[str]:
        listed.set()
        await release.wait()
        return {_WEB}

    monkeypatch.setattr(telegram_app.adb, "installed_packages", old_phone)
    lookup = asyncio.create_task(telegram_app.app_package(_SERIAL))
    await listed.wait()
    telegram_app.forget_app(_SERIAL)  # a new container started on this index meanwhile
    release.set()

    assert await lookup == _WEB
    assert _SERIAL not in telegram_app._PACKAGES, "the old phone's app is not the new one's"
