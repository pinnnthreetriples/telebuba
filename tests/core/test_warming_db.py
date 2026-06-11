"""Tests for the warming persistence helpers in ``core.db``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    add_warming_channel,
    configure_database,
    fetch_warming_state,
    list_warming_channels,
    list_warming_states,
    load_warming_settings,
    remove_warming_channel,
    save_warming_settings,
    upsert_warming_state,
)
from schemas.warming import WarmingStateWrite

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.gemini, "api_key", "")
    monkeypatch.setattr(settings.gemini, "model", "gemini-2.5-flash")


@pytest.mark.asyncio
async def test_add_channel_is_idempotent_and_ordered() -> None:
    await add_warming_channel("@first")
    await add_warming_channel("@second")
    again = await add_warming_channel("@first")

    assert [channel.channel for channel in again.channels] == ["@first", "@second"]


@pytest.mark.asyncio
async def test_remove_channel_drops_the_row() -> None:
    await add_warming_channel("@keep")
    await add_warming_channel("@drop")

    remaining = await remove_warming_channel("@drop")

    assert [channel.channel for channel in remaining.channels] == ["@keep"]


@pytest.mark.asyncio
async def test_list_channels_empty_by_default() -> None:
    channels = await list_warming_channels()

    assert channels.channels == []


@pytest.mark.asyncio
async def test_settings_default_row_is_created_on_first_read() -> None:
    secret = await load_warming_settings()

    assert secret.inter_account_chat is False
    assert secret.reactions_enabled is True
    assert secret.gemini_api_key == ""
    assert secret.gemini_model == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_save_settings_updates_and_preserves_key_when_none() -> None:
    await save_warming_settings(
        inter_account_chat=True,
        reactions_enabled=False,
        gemini_api_key="secret-key",
    )

    preserved = await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=True,
        gemini_api_key=None,
    )

    assert preserved.inter_account_chat is False
    assert preserved.reactions_enabled is True
    assert preserved.gemini_api_key == "secret-key"


@pytest.mark.asyncio
async def test_save_settings_can_clear_key_with_empty_string() -> None:
    await save_warming_settings(
        inter_account_chat=True,
        reactions_enabled=True,
        gemini_api_key="secret-key",
    )

    cleared = await save_warming_settings(
        inter_account_chat=True,
        reactions_enabled=True,
        gemini_api_key="",
    )

    assert cleared.gemini_api_key == ""


@pytest.mark.asyncio
async def test_warming_state_upsert_inserts_then_updates() -> None:
    assert await fetch_warming_state("acc-1") is None

    inserted = await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="active", cycles_completed=0),
    )
    assert inserted.state == "active"

    updated = await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="sleeping",
            cycles_completed=2,
            last_event="cycle:ok",
        ),
    )

    assert updated.state == "sleeping"
    assert updated.cycles_completed == 2
    assert updated.last_event == "cycle:ok"

    states = await list_warming_states()
    assert [record.account_id for record in states] == ["acc-1"]
