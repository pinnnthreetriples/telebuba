"""Tests for the neurocomment settings store — config fallback + saved override."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from core.config import settings
from core.db import configure_database
from schemas.neurocomment import (
    CampaignCreate,
    NeurocommentSettingsUpdate,
    UpdatePromptRequest,
)
from services.neurocomment import settings_store

if TYPE_CHECKING:
    from pathlib import Path

    from schemas.neurocomment import CommentMode


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


def test_settings_update_rejects_inverted_reply_delay_range() -> None:
    with pytest.raises(ValidationError):
        NeurocommentSettingsUpdate(
            max_comments_per_hour=1,
            max_comments_per_channel_per_day=0,
            reply_delay_min_seconds=10.0,
            reply_delay_max_seconds=1.0,
            min_trust_score=0,
        )


def test_settings_update_accepts_a_reply_delay_longer_than_a_claim_cutoff() -> None:
    """No upper cap here on purpose — the claim is heartbeaten THROUGH the delay instead.

    A cap would lock the whole Settings form: it seeds from the unbounded read model and
    resends every field, so one already-stored value above the cap would 422 every
    unrelated edit, with no field marked and the warming half possibly already saved.
    """
    update = NeurocommentSettingsUpdate(
        max_comments_per_hour=1,
        max_comments_per_channel_per_day=0,
        reply_delay_min_seconds=0.0,
        reply_delay_max_seconds=1000.0,
        min_trust_score=0,
    )
    assert update.reply_delay_max_seconds == 1000.0


@pytest.mark.asyncio
async def test_comment_mode_defaults_to_writing_first() -> None:
    """The whole point of the default: a deploy changes nothing until an operator flips it."""
    loaded = await settings_store.load_settings()
    assert loaded.comment_mode == "first"
    assert loaded.reply_wait_minutes == 10


def _mode_update(
    comment_mode: CommentMode | None = None,
    reply_wait_minutes: int | None = None,
) -> NeurocommentSettingsUpdate:
    """An update whose five limits are fixed — these cases are about the mode pair only."""
    return NeurocommentSettingsUpdate(
        max_comments_per_hour=5,
        max_comments_per_channel_per_day=2,
        reply_delay_min_seconds=1.0,
        reply_delay_max_seconds=2.0,
        min_trust_score=10,
        comment_mode=comment_mode,
        reply_wait_minutes=reply_wait_minutes,
    )


@pytest.mark.asyncio
async def test_saving_the_mode_keeps_it_and_a_limits_only_save_does_not_reset_it() -> None:
    """The Settings screen never sends the mode; that must not undo the page's toggle."""
    saved = await settings_store.save_settings(_mode_update("reply", 45))
    assert (saved.comment_mode, saved.reply_wait_minutes) == ("reply", 45)

    after_limits_only = await settings_store.save_settings(_mode_update())
    assert (after_limits_only.comment_mode, after_limits_only.reply_wait_minutes) == ("reply", 45)


def test_settings_update_rejects_an_unknown_mode_and_an_out_of_range_wait() -> None:
    with pytest.raises(ValidationError):
        # A mode the schema does not know — what a hand-rolled request body would carry.
        _mode_update(comment_mode="last")  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValidationError):
        _mode_update(reply_wait_minutes=121)


def test_campaign_create_rejects_over_long_name_and_prompt() -> None:
    with pytest.raises(ValidationError):
        CampaignCreate(name="x" * 129, prompt="p")
    with pytest.raises(ValidationError):
        CampaignCreate(name="ok", prompt="p" * 4001)


def test_update_prompt_rejects_over_long_prompt() -> None:
    with pytest.raises(ValidationError):
        UpdatePromptRequest(prompt="p" * 4001)
