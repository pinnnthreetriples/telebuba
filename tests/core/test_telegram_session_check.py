"""Tests for the missing-credentials guard in ``check_telegram_session``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database
from core.telegram_client import check_telegram_session
from core.telegram_client._session import _download_avatar_thumb
from schemas.telegram_session import TelegramSessionCheckRequest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")


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
