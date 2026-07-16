"""Error classification and client configuration tests."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from telethon import errors

from core.config import settings
from core.telegram_client import create_telegram_client, execute
from core.telegram_client._pool import TelegramClientPoolError
from schemas.device_fingerprint import TelegramClientProfile
from schemas.telegram_actions import (
    JoinChannel,
)
from tests.factories import DeviceFingerprintFactory

if TYPE_CHECKING:
    from pathlib import Path


from tests.core.telegram_client.helpers import patch_action_client as _patch_client


@pytest.mark.asyncio
async def test_execute_handles_flood_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            raise errors.FloodWaitError(request=None, capture=42)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-5", JoinChannel(channel="@hot"))

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 42
    assert result.error_type is None


@pytest.mark.asyncio
async def test_execute_handles_slow_mode_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            raise errors.SlowModeWaitError(request=None, capture=30)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-slow", JoinChannel(channel="@hot"))

    assert result.status == "slow_mode_wait"
    assert result.flood_wait_seconds == 30
    assert result.error_type is None


@pytest.mark.asyncio
async def test_execute_handles_premium_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            raise errors.FloodPremiumWaitError(request=None, capture=9)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-prem", JoinChannel(channel="@hot"))

    assert result.status == "premium_wait"
    assert result.flood_wait_seconds == 9
    assert result.error_type is None


@pytest.mark.asyncio
async def test_execute_handles_peer_flood(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            raise errors.PeerFloodError(request=None)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-peer", JoinChannel(channel="@hot"))

    assert result.status == "peer_flood"
    assert result.flood_wait_seconds is None
    assert result.error_type is None


def test_create_telegram_client_applies_flood_sleep_threshold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.telegram, "flood_sleep_threshold", 7)
    # Telethon refuses to build a client with an empty api_id/api_hash, so set
    # placeholders — CI has no .env, unlike a local dev machine.
    monkeypatch.setattr(settings.telegram, "api_id", 12345)
    monkeypatch.setattr(settings.telegram, "api_hash", "test-hash")
    profile = TelegramClientProfile(
        account_id="acc",
        session_path=str(tmp_path / "acc"),
        receive_updates=False,
        device=DeviceFingerprintFactory.build(
            account_id="acc",
            platform="linux",
            device_model="PC",
            system_version="Ubuntu 24.04",
            app_version="5.0.0 x64",
        ),
    )
    client = create_telegram_client(profile)
    try:
        assert client.flood_sleep_threshold == 7
    finally:
        if client.session is not None:
            client.session.close()


@pytest.mark.asyncio
async def test_execute_handles_generic_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            msg = "boom"
            raise RuntimeError(msg)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-6", JoinChannel(channel="@hot"))

    assert result.status == "failed"
    assert result.error_type == "RuntimeError"
    assert result.error_message == "boom"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        TelegramClientPoolError("acc-7", RuntimeError("connect failed")),
        ConnectionError("socket closed"),
        TimeoutError("handshake timed out"),
    ],
)
async def test_execute_classifies_infrastructure_failures_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
) -> None:
    """Internal connection failures are unavailable, not client failures."""

    async def failing_get_client(_account_id: str) -> object:
        raise exc

    async def fake_fetch(account_id: str) -> object:
        return MagicMock(session_name=account_id)

    monkeypatch.setattr("core.telegram_client._actions.get_client", failing_get_client)
    monkeypatch.setattr("core.telegram_client._actions.fetch_account", fake_fetch)

    result = await execute("acc-7", JoinChannel(channel="@hot"))

    assert result.status == "unavailable"
    assert result.error_type == type(exc).__name__


# --------------------------------------------------------------------------- #
# JoinDiscussionGroup — resolve the linked group from the parent, then join it
# --------------------------------------------------------------------------- #
