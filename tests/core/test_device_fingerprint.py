from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from python_socks import ProxyConnectionError
from telethon import errors

import features
from core.db import (
    configure_database,
    create_account,
    insert_device_fingerprint,
    upsert_account_proxy,
)
from core.device_fingerprint import (
    generate_random_device_fingerprint,
    get_or_create_device_fingerprint,
)
from core.telegram_client import (
    check_telegram_session,
    create_telegram_client,
    prepare_telegram_client_profile,
    telegram_client,
)
from schemas.device_fingerprint import (
    DeviceFingerprint,
    TelegramClientProfile,
    TelegramClientRequest,
)
from schemas.telegram_session import TelegramSessionCheckRequest
from tests.factories import (
    AccountCreateFactory,
    AccountProxyUpsertFactory,
    DeviceFingerprintFactory,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_device_fingerprint_created_once_in_sqlite(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")

    first = await get_or_create_device_fingerprint("account-1")
    second = await get_or_create_device_fingerprint("account-1")

    assert isinstance(first, DeviceFingerprint)
    assert second == first


@pytest.mark.asyncio
async def test_insert_duplicate_device_fingerprint_returns_saved_row(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")
    saved = DeviceFingerprintFactory.build(account_id="account-duplicate")
    changed = saved.model_copy(update={"device_model": "Laptop"})

    first = await insert_device_fingerprint(saved)
    second = await insert_device_fingerprint(changed)

    assert first == saved
    assert second == saved


@pytest.mark.asyncio
async def test_telegram_client_profile_uses_saved_fingerprint(tmp_path: Path, monkeypatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr("core.config.settings.telegram.session_dir", tmp_path / "sessions")

    request = TelegramClientRequest(account_id="account-2", receive_updates=False)
    first = await prepare_telegram_client_profile(request)
    second = await prepare_telegram_client_profile(request)

    assert first.device == second.device
    assert first.session_path == str(tmp_path / "sessions" / "account-2")
    assert first.receive_updates is False


@pytest.mark.asyncio
async def test_telegram_client_profile_includes_saved_proxy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr("core.config.settings.telegram.session_dir", tmp_path / "sessions")
    await create_account(AccountCreateFactory.build(account_id="account-proxy"))
    await upsert_account_proxy(
        AccountProxyUpsertFactory.build(
            account_id="account-proxy",
            port=9050,
            username="alice",
            password="secret",  # noqa: S106 - test fixture value, not a real credential.
        ),
    )

    profile = await prepare_telegram_client_profile(
        TelegramClientRequest(account_id="account-proxy"),
    )

    assert profile.proxy_type == "socks5"
    assert profile.proxy_host == "127.0.0.1"
    assert profile.proxy_port == 9050
    assert profile.proxy_username == "alice"
    assert profile.proxy_password == "secret"  # noqa: S105 - test fixture value.


def test_generate_random_device_fingerprint_supports_desktop_platforms(monkeypatch) -> None:
    platforms = iter(("windows", "macos", "linux"))

    def choose(options):
        if {"windows", "macos", "linux"}.issubset(set(options)):
            return next(platforms)
        return options[0]

    monkeypatch.setattr("core.device_fingerprint.secrets.choice", choose)

    assert generate_random_device_fingerprint("windows-account").platform == "windows"
    assert generate_random_device_fingerprint("macos-account").platform == "macos"
    assert generate_random_device_fingerprint("linux-account").platform == "linux"


def test_create_telegram_client_passes_device_profile(monkeypatch) -> None:
    captured = {}

    class FakeTelegramClient:
        def __init__(self, session_path: str, api_id: int, api_hash: str, **kwargs) -> None:
            captured["session_path"] = session_path
            captured["api_id"] = api_id
            captured["api_hash"] = api_hash
            captured["kwargs"] = kwargs

    monkeypatch.setattr("core.telegram_client._client.TelegramClient", FakeTelegramClient)
    monkeypatch.setattr("core.config.settings.telegram.api_id", 12345)
    monkeypatch.setattr("core.config.settings.telegram.api_hash", "hash")

    client_profile = DeviceFingerprintFactory.build(account_id="account-3")
    created = create_telegram_client(
        TelegramClientProfile(
            account_id="account-3",
            session_path="sessions/account-3",
            receive_updates=True,
            device=client_profile,
        ),
    )

    assert isinstance(created, FakeTelegramClient)
    assert captured == {
        "session_path": "sessions/account-3",
        "api_id": 12345,
        "api_hash": "hash",
        "kwargs": {
            "device_model": "Desktop",
            "system_version": "Windows 11",
            "app_version": "5.4.0 x64",
            "lang_code": "en",
            "system_lang_code": "en-US",
            "receive_updates": True,
            "timeout": 20,
            "connection_retries": 3,
            "retry_delay": 2,
            "request_retries": 3,
            "flood_sleep_threshold": 0,
        },
    }


def test_create_telegram_client_passes_proxy(monkeypatch) -> None:
    captured = {}

    class FakeTelegramClient:
        def __init__(self, _session_path: str, _api_id: int, _api_hash: str, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("core.telegram_client._client.TelegramClient", FakeTelegramClient)

    client_profile = DeviceFingerprintFactory.build(account_id="account-3")
    create_telegram_client(
        TelegramClientProfile(
            account_id="account-3",
            session_path="sessions/account-3",
            receive_updates=True,
            device=client_profile,
            proxy_type="http",
            proxy_host="proxy.local",
            proxy_port=8080,
            proxy_username="bob",
            proxy_password="pw",  # noqa: S106 - test fixture value, not a real credential.
        ),
    )

    assert captured["proxy"] == {
        "proxy_type": "http",
        "addr": "proxy.local",
        "port": 8080,
        "rdns": True,
        "username": "bob",
        "password": "pw",
    }


@pytest.mark.asyncio
async def test_telegram_client_context_disconnects(tmp_path: Path, monkeypatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr("core.config.settings.telegram.session_dir", tmp_path / "sessions")
    disconnected = False

    class FakeTelegramClient:
        async def disconnect(self) -> None:
            nonlocal disconnected
            disconnected = True

    monkeypatch.setattr(
        "core.telegram_client._client.create_telegram_client",
        lambda _: FakeTelegramClient(),
    )

    async with telegram_client(TelegramClientRequest(account_id="account-4")) as client:
        assert isinstance(client, FakeTelegramClient)

    assert disconnected is True
    assert features is not None


@pytest.mark.asyncio
async def test_check_telegram_session_returns_alive(tmp_path: Path, monkeypatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr("core.config.settings.telegram.session_dir", tmp_path / "sessions")
    monkeypatch.setattr("core.config.settings.telegram.api_id", 12345)
    monkeypatch.setattr("core.config.settings.telegram.api_hash", "hash")
    fake_client = FakeSessionClient(authorized=True)
    monkeypatch.setattr(
        "core.telegram_client._session.create_telegram_client", lambda _: fake_client
    )

    result = await check_telegram_session(TelegramSessionCheckRequest(account_id="account-alive"))

    assert result.status == "alive"
    assert result.is_temporary is False
    assert result.user_id == 123
    assert result.username == "user"
    assert fake_client.disconnected is True


@pytest.mark.asyncio
async def test_check_telegram_session_returns_unauthorized(tmp_path: Path, monkeypatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr("core.config.settings.telegram.session_dir", tmp_path / "sessions")
    monkeypatch.setattr("core.config.settings.telegram.api_id", 12345)
    monkeypatch.setattr("core.config.settings.telegram.api_hash", "hash")
    fake_client = FakeSessionClient(authorized=False)
    monkeypatch.setattr(
        "core.telegram_client._session.create_telegram_client", lambda _: fake_client
    )

    result = await check_telegram_session(TelegramSessionCheckRequest(account_id="account-dead"))

    assert result.status == "unauthorized"
    assert result.is_temporary is False
    assert result.user_id is None
    assert fake_client.disconnected is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (TimeoutError("timeout"), "network_error"),
        (ProxyConnectionError("proxy down"), "proxy_error"),
        (errors.SessionRevokedError(request=None), "session_error"),
        (errors.UserDeactivatedBanError(request=None), "account_error"),
        (errors.FloodWaitError(request=None, capture=42), "flood_wait"),
    ],
)
async def test_check_telegram_session_classifies_errors(
    tmp_path: Path,
    monkeypatch,
    exc: Exception,
    status: str,
) -> None:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr("core.config.settings.telegram.session_dir", tmp_path / "sessions")
    monkeypatch.setattr("core.config.settings.telegram.api_id", 12345)
    monkeypatch.setattr("core.config.settings.telegram.api_hash", "hash")
    fake_client = FakeSessionClient(connect_error=exc)
    monkeypatch.setattr(
        "core.telegram_client._session.create_telegram_client", lambda _: fake_client
    )

    result = await check_telegram_session(TelegramSessionCheckRequest(account_id="account-error"))

    assert result.status == status
    assert result.is_temporary is (status in {"network_error", "proxy_error", "flood_wait"})
    assert result.error_type == type(exc).__name__
    assert fake_client.disconnected is True
    if status == "flood_wait":
        assert result.flood_wait_seconds == 42


class FakeTelegramUser:
    id = 123
    phone = "100200300"
    username = "user"
    first_name = "First"
    last_name = "Last"


class FakeSessionClient:
    def __init__(
        self,
        *,
        authorized: bool = True,
        connect_error: Exception | None = None,
    ) -> None:
        self.authorized = authorized
        self.connect_error = connect_error
        self.disconnected = False

    async def connect(self) -> None:
        if self.connect_error is not None:
            raise self.connect_error

    async def is_user_authorized(self) -> bool:
        return self.authorized

    async def get_me(self) -> FakeTelegramUser:
        return FakeTelegramUser()

    async def disconnect(self) -> None:
        self.disconnected = True
