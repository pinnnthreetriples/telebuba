"""Tests for the warming persistence helpers in ``core.db``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    add_warming_channel,
    configure_database,
    create_account,
    fetch_warming_state,
    list_warming_channels,
    list_warming_states,
    load_warming_settings,
    mark_promoted_to_nc,
    remove_warming_channel,
    save_warming_settings,
    upsert_warming_state,
)
from schemas.accounts import AccountCreate
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
async def test_save_ignores_gemini_key_and_reads_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The DB no longer stores Gemini credentials; load returns the env value."""
    monkeypatch.setattr(settings.gemini, "api_key", "env-key")

    saved = await save_warming_settings(
        inter_account_chat=True,
        reactions_enabled=False,
        gemini_api_key="ui-typed-this-but-it-must-be-ignored",
    )

    assert saved.gemini_api_key == "env-key"

    reloaded = await load_warming_settings()
    assert reloaded.gemini_api_key == "env-key"


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
    # Parent account row required now FK is enforced.
    await create_account(AccountCreate(account_id="acc-1", label="A", session_name="acc-1"))
    assert await fetch_warming_state("acc-1") is None

    inserted = await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="active", cycles_completed=0),
    )
    assert inserted.record.state == "active"

    updated = await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="sleeping",
            cycles_completed=2,
            last_event="cycle:ok",
        ),
    )

    assert updated.record.state == "sleeping"
    assert updated.record.cycles_completed == 2
    assert updated.record.last_event == "cycle:ok"

    states = await list_warming_states()
    assert [record.account_id for record in states] == ["acc-1"]


@pytest.mark.asyncio
async def test_settings_join_enabled_defaults_on_and_roundtrips() -> None:
    secret = await load_warming_settings()
    assert secret.join_enabled is True

    saved = await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=True,
        join_enabled=False,
        gemini_api_key=None,
    )

    assert saved.join_enabled is False


@pytest.mark.asyncio
async def test_settings_warming_controls_default_and_roundtrip() -> None:
    secret = await load_warming_settings()
    assert secret.enforce_readiness is True
    assert secret.quiet_hours_enabled is False
    assert secret.max_daily_actions == 0

    saved = await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=True,
        enforce_readiness=False,
        quiet_hours_enabled=True,
        quiet_hours_start=1,
        quiet_hours_end=6,
        max_daily_actions=30,
        gemini_api_key=None,
    )

    assert saved.enforce_readiness is False
    assert saved.quiet_hours_enabled is True
    assert saved.quiet_hours_start == 1
    assert saved.quiet_hours_end == 6
    assert saved.max_daily_actions == 30


@pytest.mark.asyncio
async def test_warming_state_persists_proxy_snapshot_and_daily_fields() -> None:
    await create_account(AccountCreate(account_id="acc-1"))

    result = await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="active",
            proxy_snapshot="socks5://1.2.3.4:1080",
            daily_actions=7,
            daily_count_date="2026-06-12",
        ),
    )

    assert result.record.proxy_snapshot == "socks5://1.2.3.4:1080"
    assert result.record.daily_actions == 7
    assert result.record.daily_count_date == "2026-06-12"

    again = await fetch_warming_state("acc-1")
    assert again is not None
    assert again.daily_actions == 7


@pytest.mark.asyncio
async def test_mark_promoted_to_nc_rejects_unknown_account() -> None:
    """Bug 14: upserting promoted_to_nc for a non-existent account would create a ghost row."""
    with pytest.raises(ValueError, match="unknown account_id"):
        await mark_promoted_to_nc("does-not-exist")

    # And no warming-state ghost row was left behind.
    assert await fetch_warming_state("does-not-exist") is None
