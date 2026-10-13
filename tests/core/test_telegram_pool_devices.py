"""The pool keys clients by ``(account_id, device)``: an account's two authorizations.

A desktop and a mobile device are two sign-ins with two ``.session`` files, so their
clients must live side by side — the listener on one, warming on the other — without
either borrow evicting or reconnecting the other. A borrower that names no device gets
the account's primary one, and a named device the account lacks is refused, never
served by the other device.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

import pytest

from core.config import settings
from core.db import _get_engine, configure_database, create_account, insert_device_fingerprint
from core.repositories.device_fingerprint import record_second_device
from core.telegram_client import _pool as _pool_module
from core.telegram_client._pool import (
    _CLIENTS,
    _PRIMARY,
    _reset_for_tests,
    evict_client,
    forget_primary_device,
    get_client,
    register_rebuild_hook,
    removing_client,
)
from core.telegram_client._pool_errors import (
    TelegramClientPoolError,
    TelegramDeviceUnavailableError,
)
from core.telegram_client._pool_hooks import _REBUILD_HOOKS
from schemas.accounts import AccountCreate
from schemas.device_fingerprint import DeviceFingerprint

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from schemas.device_fingerprint import DeviceType, TelegramClientProfile


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    _get_engine()
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    _reset_for_tests()
    yield
    _reset_for_tests()


class _FakeClient:
    def __init__(self, device: DeviceType) -> None:
        self.device = device
        self.connect_calls = 0
        self.disconnect_calls = 0
        self._connected = False

    async def connect(self) -> None:
        self.connect_calls += 1
        self._connected = True

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> list[_FakeClient]:
    """Every client the pool builds, tagged with the device its real profile names."""
    monkeypatch.setattr(settings.telegram, "mobile_api_id", 6)
    monkeypatch.setattr(settings.telegram, "mobile_api_hash", "mobile-hash")
    clients: list[_FakeClient] = []

    def fake_create(profile: TelegramClientProfile) -> _FakeClient:
        assert profile.device.device_type == profile.device_type
        client = _FakeClient(profile.device_type)
        clients.append(client)
        return client

    monkeypatch.setattr("core.telegram_client._pool.create_telegram_client", fake_create)
    return clients


def _fingerprint(account_id: str, device_type: DeviceType) -> DeviceFingerprint:
    mobile = device_type == "mobile"
    return DeviceFingerprint(
        account_id=account_id,
        device_type=device_type,
        platform="android" if mobile else "windows",
        device_model="GooglePixel 8" if mobile else "Desktop PC",
        system_version="SDK 34" if mobile else "Windows 11",
        app_version="12.10.6 (7112)" if mobile else "5.6.3 x64",
        lang_code="en",
        system_lang_code="en-US",
    )


async def _borrow(account_id: str, device_type: DeviceType | None = None) -> _FakeClient:
    return cast("_FakeClient", await get_client(account_id, device_type))


async def _account(account_id: str, *devices: DeviceType) -> None:
    primary, *second = devices
    await create_account(
        AccountCreate(account_id=account_id, session_name=account_id, session_device=primary),
    )
    await insert_device_fingerprint(_fingerprint(account_id, primary))
    if second:
        await record_second_device(f"{account_id}_{second[0]}", _fingerprint(account_id, second[0]))


@pytest.mark.asyncio
async def test_desktop_and_mobile_clients_serve_side_by_side_without_reconnecting(
    built: list[_FakeClient],
) -> None:
    """The listener borrows the primary (desktop) while warming borrows mobile."""
    await _account("acc", "desktop", "mobile")

    borrows = await asyncio.gather(
        *(_borrow("acc") for _ in range(10)),
        *(_borrow("acc", "mobile") for _ in range(10)),
    )
    listener, warming = borrows[0], borrows[-1]

    assert {client.device for client in built} == {"desktop", "mobile"}
    assert len(built) == 2, "one client per device, however the borrows interleave"
    assert all(client is listener for client in borrows[:10])
    assert all(client is warming for client in borrows[10:])
    assert [(c.connect_calls, c.disconnect_calls) for c in built] == [(1, 0), (1, 0)]


@pytest.mark.asyncio
@pytest.mark.usefixtures("built")
async def test_a_borrower_naming_no_device_gets_the_primary() -> None:
    await _account("both", "mobile", "desktop")
    await _account("phone-only", "mobile")

    assert (await _borrow("both")).device == "desktop"
    assert (await _borrow("phone-only")).device == "mobile"
    assert ("phone-only", "desktop") not in _CLIENTS, "no desktop is invented for a phone"


@pytest.mark.asyncio
async def test_a_device_the_account_lacks_is_refused_not_substituted(
    built: list[_FakeClient],
) -> None:
    await _account("desk", "desktop")

    with pytest.raises(TelegramDeviceUnavailableError) as refused:
        await _borrow("desk", "mobile")

    assert refused.value.code == "device_missing"
    assert isinstance(refused.value, TelegramClientPoolError), (
        "borrowers classify it as unavailable"
    )
    assert built == [], "nothing was built for the missing device, and no other device either"


@pytest.mark.asyncio
@pytest.mark.usefixtures("built")
async def test_removing_one_device_leaves_the_other_in_service() -> None:
    await _account("acc", "desktop", "mobile")
    desktop = await _borrow("acc")
    mobile = await _borrow("acc", "mobile")

    async with removing_client("acc", "mobile"):
        assert mobile.disconnect_calls == 1
        assert await _borrow("acc") is desktop
        with pytest.raises(TelegramClientPoolError):
            await _borrow("acc", "mobile")

    assert desktop.disconnect_calls == 0


@pytest.mark.asyncio
async def test_evicting_the_account_takes_every_device(built: list[_FakeClient]) -> None:
    await _account("acc", "desktop", "mobile")
    await _borrow("acc")
    await _borrow("acc", "mobile")

    await evict_client("acc")

    assert [client.disconnect_calls for client in built] == [1, 1]
    assert not [key for key in _CLIENTS if key[0] == "acc"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("built")
async def test_standing_subscriptions_reattach_on_the_primary_device_only() -> None:
    await _account("acc", "desktop", "mobile")
    seen: list[str] = []

    async def hook(_account_id: str, client: _FakeClient) -> None:
        seen.append(client.device)

    register_rebuild_hook(hook)  # ty: ignore[invalid-argument-type]
    try:
        await _borrow("acc", "mobile")
        await _borrow("acc")
    finally:
        _REBUILD_HOOKS.remove(hook)  # ty: ignore[invalid-argument-type]

    assert seen == ["desktop"], "a second handler on the other device would deliver twice"


@pytest.mark.asyncio
@pytest.mark.usefixtures("built")
async def test_a_device_added_later_becomes_the_primary_for_new_borrows() -> None:
    await _account("acc", "mobile")
    mobile = await _borrow("acc")
    await record_second_device("acc_desktop", _fingerprint("acc", "desktop"))

    forget_primary_device("acc")

    assert (await _borrow("acc")).device == "desktop"
    assert await _borrow("acc", "mobile") is mobile, "the open mobile client keeps serving"


@pytest.mark.asyncio
async def test_a_removal_drops_the_primary_device_a_borrow_cached() -> None:
    """A borrow racing the removal may cache a device for a row that is about to go."""
    async with removing_client("acc"):
        _PRIMARY["acc"] = "desktop"  # what a borrow resolved mid-removal

    assert "acc" not in _PRIMARY


@pytest.mark.asyncio
async def test_a_primary_read_that_raced_a_device_change_is_not_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The row read started before the device change; its answer may predate it."""
    real = _pool_module.fetch_account

    async def read_while_a_device_is_added(account_id: str):
        account = await real(account_id)
        forget_primary_device(account_id)  # the change commits while the read is out
        return account

    await _account("acc", "mobile")
    monkeypatch.setattr(_pool_module, "fetch_account", read_while_a_device_is_added)

    assert await _pool_module._primary_device("acc") == "mobile"
    assert "acc" not in _PRIMARY, "the next borrow resolves it afresh"
