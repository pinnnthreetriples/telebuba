"""Tests for the neurocomment settings store — config fallback + saved override."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database
from schemas.neurocomment import NeurocommentSettingsUpdate
from services.neurocomment import settings_store

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")


@pytest.mark.asyncio
async def test_load_falls_back_to_config_when_unsaved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.neurocomment, "max_comments_per_hour", 7)
    monkeypatch.setattr(settings.neurocomment, "min_trust_score", 0)

    loaded = await settings_store.load_settings()

    # No row saved → live config (and a later config change would show through).
    assert loaded.max_comments_per_hour == 7
    assert loaded.min_trust_score == 0


@pytest.mark.asyncio
async def test_save_then_load_returns_the_override() -> None:
    saved = await settings_store.save_settings(
        NeurocommentSettingsUpdate(
            max_comments_per_hour=3,
            max_comments_per_channel_per_day=1,
            reply_delay_min_seconds=2.0,
            reply_delay_max_seconds=4.0,
            min_trust_score=60,
        ),
    )
    assert saved.max_comments_per_hour == 3
    assert saved.min_trust_score == 60

    reloaded = await settings_store.load_settings()
    assert reloaded.max_comments_per_channel_per_day == 1
    assert reloaded.reply_delay_max_seconds == 4.0
    assert reloaded.min_trust_score == 60


@pytest.mark.asyncio
async def test_save_overrides_live_config(monkeypatch: pytest.MonkeyPatch) -> None:
    await settings_store.save_settings(
        NeurocommentSettingsUpdate(
            max_comments_per_hour=2,
            max_comments_per_channel_per_day=0,
            reply_delay_min_seconds=1.0,
            reply_delay_max_seconds=1.0,
            min_trust_score=50,
        ),
    )
    # A config change no longer wins once an explicit override is stored.
    monkeypatch.setattr(settings.neurocomment, "max_comments_per_hour", 99)
    reloaded = await settings_store.load_settings()
    assert reloaded.max_comments_per_hour == 2
