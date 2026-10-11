"""Tests for desktop/mobile device_type switching in ``core.telegram_client._pool``."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from core.config import settings
from core.db import add_account, configure_database, upsert_device_fingerprint
from core.logging import reset_logging_for_tests, setup_logging
from core.telegram_client._actions import execute
from core.telegram_client._client import prepare_telegram_client_profile
from core.telegram_client._pool import (
    _CLIENT_DEVICE_TYPES,
    _reset_for_tests,
    evict_client,
    get_client,
)
from schemas.accounts import AccountCreate
from schemas.device_fingerprint import (
    DeviceFingerprint,
    DeviceType,
    TelegramClientRequest,
)
from schemas.telegram_actions import SetOnline

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    configure_database(tmp_path / "telebuba_pool_dt.db")
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    _reset_for_tests()
    yield
    _reset_for_tests()
    reset_logging_for_tests()


class _FakeSwitchClient:
    def __init__(self, device_type: DeviceType, events: list[str]) -> None:
        self.device_type = device_type
        self._events = events
        self.connect_calls = 0
        self.disconnect_calls = 0
        self._connected = False

    async def connect(self) -> None:
        self.connect_calls += 1
        self._connected = True
        self._events.append(f"connect:{self.device_type}")

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self._connected = False
        self._events.append(f"disconnect:{self.device_type}")

    def is_connected(self) -> bool:
        return self._connected

    async def __call__(self, _request: object) -> object:
        return True


def _install_order_tracking_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[_FakeSwitchClient], list[str], list[DeviceType]]:
    built: list[_FakeSwitchClient] = []
    events: list[str] = []
    requested_types: list[DeviceType] = []

    async def fake_prepare(request: TelegramClientRequest) -> object:
        requested_types.append(request.device_type)
        return MagicMock(
            account_id=request.account_id,
            session_path="memory://test",
            device_type=request.device_type,
        )

    def fake_create(profile: object) -> _FakeSwitchClient:
        dt: DeviceType = getattr(profile, "device_type", "desktop")
        client = _FakeSwitchClient(dt, events)
        built.append(client)
        return client

    monkeypatch.setattr("core.telegram_client._pool.prepare_telegram_client_profile", fake_prepare)
    monkeypatch.setattr("core.telegram_client._pool.create_telegram_client", fake_create)
    return built, events, requested_types


@pytest.mark.asyncio
async def test_get_client_defaults_to_desktop_and_reuses_same_device_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built, events, requested_types = _install_order_tracking_factory(monkeypatch)

    first = await get_client("acc-1")
    second = await get_client("acc-1", device_type="desktop")

    assert first is second
    assert len(built) == 1
    assert requested_types == ["desktop"]
    assert _CLIENT_DEVICE_TYPES["acc-1"] == "desktop"
    assert events == ["connect:desktop"]


@pytest.mark.asyncio
async def test_get_client_switches_device_type_by_disconnecting_before_reconnecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built, events, requested_types = _install_order_tracking_factory(monkeypatch)

    desktop_client = await get_client("acc-switch", device_type="desktop")
    mobile_client = await get_client("acc-switch", device_type="mobile")
    mobile_again = await get_client("acc-switch", device_type="mobile")

    assert mobile_client is not desktop_client
    assert mobile_again is mobile_client
    assert len(built) == 2
    assert built[0].disconnect_calls == 1
    assert built[1].connect_calls == 1
    assert requested_types == ["desktop", "mobile"]
    assert _CLIENT_DEVICE_TYPES["acc-switch"] == "mobile"
    # Strict ordering invariant: old client MUST disconnect before new client connects.
    assert events == ["connect:desktop", "disconnect:desktop", "connect:mobile"]

    # Switch back to desktop
    desktop_again = await get_client("acc-switch", device_type="desktop")
    assert desktop_again is not mobile_client
    assert _CLIENT_DEVICE_TYPES["acc-switch"] == "desktop"
    assert events == [
        "connect:desktop",
        "disconnect:desktop",
        "connect:mobile",
        "disconnect:mobile",
        "connect:desktop",
    ]

    await evict_client("acc-switch")
    assert "acc-switch" not in _CLIENT_DEVICE_TYPES


@pytest.mark.asyncio
async def test_prepare_telegram_client_profile_loads_matching_device_type() -> None:
    await add_account(AccountCreate(account_id="acc-prof", phone="+79990001122"))
    await upsert_device_fingerprint(
        DeviceFingerprint(
            account_id="acc-prof",
            device_type="desktop",
            platform="windows",
            device_model="PC 64bit",
            system_version="Windows 11",
            app_version="5.2.3 x64",
            lang_code="ru",
            system_lang_code="ru-RU",
        )
    )
    await upsert_device_fingerprint(
        DeviceFingerprint(
            account_id="acc-prof",
            device_type="mobile",
            platform="android",
            device_model="Samsung Galaxy S24 Ultra",
            system_version="SDK 34 (Android 14)",
            app_version="Telegram Android 10.9.2",
            lang_code="ru",
            system_lang_code="ru-RU",
        )
    )

    desktop_profile = await prepare_telegram_client_profile(
        TelegramClientRequest(account_id="acc-prof", device_type="desktop")
    )
    mobile_profile = await prepare_telegram_client_profile(
        TelegramClientRequest(account_id="acc-prof", device_type="mobile")
    )

    assert desktop_profile.device.device_type == "desktop"
    assert desktop_profile.device.device_model == "PC 64bit"
    assert mobile_profile.device.device_type == "mobile"
    assert mobile_profile.device.device_model == "Samsung Galaxy S24 Ultra"


@pytest.mark.asyncio
async def test_execute_forwards_device_type_to_get_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await add_account(AccountCreate(account_id="acc-exec", phone="+79990001133"))
    seen_calls: list[tuple[str, str]] = []
    fake_client = _FakeSwitchClient("mobile", [])

    async def fake_get_client(account_id: str, device_type: DeviceType = "desktop") -> object:
        seen_calls.append((account_id, device_type))
        return fake_client

    monkeypatch.setattr("core.telegram_client._actions.get_client", fake_get_client)

    res = await execute("acc-exec", SetOnline(online=True), device_type="mobile")
    assert res.status == "ok"
    assert seen_calls == [("acc-exec", "mobile")]
