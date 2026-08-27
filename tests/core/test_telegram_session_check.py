"""Tests for the missing-credentials guard and the pooled client in ``check_telegram_session``."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from python_socks import ProxyConnectionError

from core.config import settings
from core.db import configure_database
from core.telegram_client import check_telegram_session
from core.telegram_client._pool import (
    TelegramClientPoolError,
    _connect_lock,
    _reset_for_tests,
    removing_client,
)
from core.telegram_client._session import _download_avatar_thumb
from schemas.telegram_session import TelegramSessionCheckRequest

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    # The deadline tests below take a real per-account lock, and an ``asyncio.Lock``
    # binds the loop it was first awaited on — leaving one in the module dict would
    # break the next test's fresh loop.
    _reset_for_tests()
    yield
    _reset_for_tests()


@pytest.mark.asyncio
async def test_missing_api_id_returns_typed_session_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.telegram, "api_id", 0)
    monkeypatch.setattr(settings.telegram, "api_hash", "any")

    result = await check_telegram_session(
        TelegramSessionCheckRequest(account_id="acc-1"),
    )

    assert result.status == "session_error"
    assert result.error_type == "MissingCredentials"
    assert "TELEGRAM__API_ID" in (result.error_message or "")


class _AvatarClient:
    def __init__(self, result: object) -> None:
        self._result = result

    async def download_profile_photo(
        self,
        entity: object,  # noqa: ARG002
        *,
        file: object,  # noqa: ARG002
        download_big: bool,  # noqa: ARG002
    ) -> object:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


@pytest.mark.asyncio
async def test_download_avatar_thumb_returns_bytes() -> None:
    assert await _download_avatar_thumb(_AvatarClient(b"jpeg"), object()) == b"jpeg"


@pytest.mark.asyncio
async def test_download_avatar_thumb_swallows_download_errors() -> None:
    # A refused download (FloodWait/RPC/no photo) must never fail the check.
    assert await _download_avatar_thumb(_AvatarClient(RuntimeError("flood")), object()) is None


@pytest.mark.asyncio
async def test_download_avatar_thumb_none_when_no_photo() -> None:
    assert await _download_avatar_thumb(_AvatarClient(None), object()) is None


class _PooledClient:
    """Minimal pooled-client stand-in: authorized, no app config, no avatar."""

    async def is_user_authorized(self) -> bool:
        return True

    async def get_me(self) -> object:
        return SimpleNamespace(id=7, phone="79990000000", username="u", first_name="F")

    async def __call__(self, _request: object) -> object:
        # The freeze probe degrades to "not frozen" on any failure.
        msg = "no app config"
        raise RuntimeError(msg)

    async def download_profile_photo(self, _me: object, **_kwargs: object) -> None:
        return None


def _with_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.telegram, "api_id", 12345)
    monkeypatch.setattr(settings.telegram, "api_hash", "hash")


@pytest.mark.asyncio
async def test_check_borrows_the_pooled_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """The check must not open a second client on the account's ``.session`` file.

    Telethon holds that SQLite file in an uncommitted write transaction while a
    client is connected, so a throwaway probe raised "database is locked" and
    500'd the Accounts screen for every account something else was holding.
    """
    _with_credentials(monkeypatch)
    borrowed: list[str] = []

    async def fake_get_client(account_id: str) -> object:
        borrowed.append(account_id)
        return _PooledClient()

    monkeypatch.setattr("core.telegram_client._session.get_client", fake_get_client)

    result = await check_telegram_session(TelegramSessionCheckRequest(account_id="acc-1"))

    assert borrowed == ["acc-1"]
    assert result.status == "alive"
    assert result.user_id == 7


@pytest.mark.asyncio
async def test_pool_connect_failure_is_classified_by_its_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pool error is a wrapper: the verdict must come from the error it wraps."""
    _with_credentials(monkeypatch)

    async def failing_get_client(account_id: str) -> object:
        raise TelegramClientPoolError(account_id, ProxyConnectionError("proxy refused"))

    monkeypatch.setattr("core.telegram_client._session.get_client", failing_get_client)

    result = await check_telegram_session(TelegramSessionCheckRequest(account_id="acc-1"))

    # proxy_error, not the unknown_error a naked TelegramClientPoolError would give.
    assert result.status == "proxy_error"
    assert result.is_temporary is True


@pytest.mark.asyncio
async def test_a_live_tombstone_does_not_become_an_unknown_error_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A login / logout / removal in flight is not a verdict about the session.

    ``removing_client`` refuses the borrow, and that refusal used to arrive as a
    bare ``RuntimeError`` cause matching no arm in the ladder — so the catch-all
    said ``unknown_error``, which ``services.accounts.sessions`` then PERSISTED on
    a row whose session was perfectly fine, until the next manual check.
    """
    _with_credentials(monkeypatch)

    async with removing_client("acc-1"):
        result = await check_telegram_session(TelegramSessionCheckRequest(account_id="acc-1"))

    assert result.status == "network_error"
    assert result.is_temporary is True
    assert result.error_type == "TelegramClientUnavailableError"


class _MuteClient:
    """A borrowed client whose first RPC never answers (half-open socket)."""

    async def is_user_authorized(self) -> bool:
        await asyncio.Event().wait()
        return True


@pytest.mark.asyncio
async def test_a_silent_pooled_client_cannot_hang_the_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The RPC that never answers must end as a verdict, not as a forever-spinner.

    Telethon's ``users._call`` awaits the response future with no timeout, and a
    pooled client whose socket went half-open still reports ``is_connected()``
    True — so the check borrowed it on the pool's fast path and blocked for good.
    """
    _with_credentials(monkeypatch)
    monkeypatch.setattr(settings.telegram, "session_check_timeout_seconds", 0.05)

    async def fake_get_client(_account_id: str) -> object:
        return _MuteClient()

    monkeypatch.setattr("core.telegram_client._session.get_client", fake_get_client)

    result = await check_telegram_session(TelegramSessionCheckRequest(account_id="acc-1"))

    assert result.status == "network_error"
    assert result.is_temporary is True
    assert result.error_type == "TimeoutError"


@pytest.mark.asyncio
async def test_a_held_connect_lock_cannot_hang_the_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pool's per-account connect lock is acquired with no timeout of its own.

    A borrower already inside ``get_client`` for this account keeps every other
    borrower queued, so the check must carry its own deadline to the lock too.
    """
    _with_credentials(monkeypatch)
    monkeypatch.setattr(settings.telegram, "session_check_timeout_seconds", 0.05)

    async with _connect_lock("acc-1"):
        result = await check_telegram_session(TelegramSessionCheckRequest(account_id="acc-1"))

    assert result.status == "network_error"
    assert result.is_temporary is True
    assert result.error_type == "TimeoutError"


@pytest.mark.asyncio
async def test_missing_api_hash_returns_typed_session_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.telegram, "api_id", 12345)
    monkeypatch.setattr(settings.telegram, "api_hash", "")

    result = await check_telegram_session(
        TelegramSessionCheckRequest(account_id="acc-2"),
    )

    assert result.status == "session_error"
    assert result.error_type == "MissingCredentials"
