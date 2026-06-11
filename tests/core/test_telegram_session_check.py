"""Tests for the missing-credentials guard in ``check_telegram_session``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database
from core.telegram_client import check_telegram_session
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
