"""``has_live_client`` — does the pool already hold a connected client, without making one."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from core.telegram_client import _pool as pool
from core.telegram_client import has_live_client

if TYPE_CHECKING:
    from collections.abc import Iterator

    from telethon import TelegramClient

    from schemas.device_fingerprint import DeviceType


class _Client:
    def __init__(self, *, connected: bool = True, authorized: bool | None = None) -> None:
        self._connected = connected
        self._authorized = authorized
        self.calls: list[str] = []

    def is_connected(self) -> bool:
        return self._connected

    def __getattr__(self, name: str) -> object:
        # Anything that would be I/O is recorded; the check must not call it.
        self.calls.append(name)
        raise AssertionError(name)


def _install(account_id: str, device: DeviceType, client: _Client) -> None:
    pool._CLIENTS[(account_id, device)] = cast("TelegramClient", client)


@pytest.fixture(autouse=True)
def _clean_pool() -> Iterator[None]:
    pool._reset_for_tests()
    yield
    pool._reset_for_tests()


def test_an_account_with_no_client_is_not_live() -> None:
    assert has_live_client("acc-1") is False


def test_a_connected_client_is_live_and_nothing_is_called_on_it() -> None:
    client = _Client()
    _install("acc-1", "desktop", client)

    assert has_live_client("acc-1") is True
    assert client.calls == []


def test_a_disconnected_client_is_not_live() -> None:
    _install("acc-1", "desktop", _Client(connected=False))

    assert has_live_client("acc-1") is False


def test_a_client_known_to_be_unauthorised_is_not_live() -> None:
    _install("acc-1", "desktop", _Client(authorized=False))

    assert has_live_client("acc-1") is False


def test_another_accounts_client_does_not_count() -> None:
    _install("acc-2", "desktop", _Client())

    assert has_live_client("acc-1") is False


def test_a_known_primary_device_is_the_one_that_counts() -> None:
    _install("acc-1", "mobile", _Client())
    pool._PRIMARY["acc-1"] = "desktop"

    assert has_live_client("acc-1") is False  # the probe would borrow the desktop client

    pool._PRIMARY["acc-1"] = "mobile"
    assert has_live_client("acc-1") is True


def test_an_account_being_removed_or_a_pool_shutting_down_is_not_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install("acc-1", "desktop", _Client())
    monkeypatch.setitem(pool._REMOVING, "acc-1", 1)
    assert has_live_client("acc-1") is False

    monkeypatch.delitem(pool._REMOVING, "acc-1")
    monkeypatch.setattr(pool, "_SHUTTING_DOWN", True)
    assert has_live_client("acc-1") is False
