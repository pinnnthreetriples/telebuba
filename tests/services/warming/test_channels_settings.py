"""Warming tests split from the former service test module: test_channels_settings.py."""

from __future__ import annotations

import pytest

from core.config import settings
from core.db import (
    _get_engine,
    create_account,
    save_warming_settings,
)
from schemas.accounts import AccountCreate
from schemas.warming import (
    AddChannelsRequest,
    RemoveChannelRequest,
    WarmingSettingsUpdate,
)
from services import warming


@pytest.mark.asyncio
async def test_add_channels_parses_and_dedupes() -> None:
    # Normalization now strips a leading ``@`` and requires usernames of
    # length 3-32 — pick names that satisfy Telegram's minimum.
    result = await warming.add_channels(
        AddChannelsRequest(raw="@alpha, https://t.me/beta\n@alpha\n  gamma  "),
    )

    assert [channel.channel for channel in result.channels] == ["alpha", "beta", "gamma"]


@pytest.mark.asyncio
async def test_save_settings_returns_masked_view() -> None:
    masked = await warming.save_settings(
        WarmingSettingsUpdate(inter_account_chat=True, reactions_enabled=False, gemini_api_key="k"),
    )

    assert masked.inter_account_chat is True
    assert masked.reactions_enabled is False
    assert masked.has_gemini_key is True
    assert not hasattr(masked, "gemini_api_key")


@pytest.mark.asyncio
async def test_list_and_remove_channels_roundtrip() -> None:
    await warming.add_channels(AddChannelsRequest(raw="@alpha\n@beta"))

    listed = await warming.list_channels()
    assert {channel.channel for channel in listed.channels} == {"alpha", "beta"}

    remaining = await warming.remove_channel(RemoveChannelRequest(channel="alpha"))
    assert [channel.channel for channel in remaining.channels] == ["beta"]


@pytest.mark.asyncio
async def test_load_settings_masks_key() -> None:
    await save_warming_settings(
        inter_account_chat=True,
        reactions_enabled=False,
        gemini_api_key="secret",
    )

    masked = await warming.load_settings()

    assert masked.has_gemini_key is True
    assert masked.inter_account_chat is True
    assert masked.reactions_enabled is False


@pytest.mark.asyncio
async def test_save_settings_persists_gemini_key_and_model() -> None:
    """A UI-typed Gemini key + model are persisted (has_gemini_key True, model stored)."""
    masked = await warming.save_settings(
        WarmingSettingsUpdate(
            inter_account_chat=False,
            reactions_enabled=False,
            gemini_api_key="ui-key",
            gemini_model="ui-model",
        ),
    )
    assert masked.has_gemini_key is True
    assert masked.gemini_model == "ui-model"


@pytest.mark.asyncio
async def test_save_settings_persists_gemini_tuning() -> None:
    """The retry count + inter-call interval round-trip through the masked view."""
    masked = await warming.save_settings(
        WarmingSettingsUpdate(
            inter_account_chat=False,
            reactions_enabled=False,
            gemini_max_retries=3,
            gemini_min_interval_seconds=4.0,
        ),
    )
    assert masked.gemini_max_retries == 3
    assert masked.gemini_min_interval_seconds == 4.0

    reloaded = await warming.load_settings()
    assert reloaded.gemini_max_retries == 3
    assert reloaded.gemini_min_interval_seconds == 4.0


@pytest.mark.asyncio
async def test_save_settings_clear_gemini_key_falls_back_to_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """clear_gemini_key wipes the stored key; the read then falls back to .env."""
    monkeypatch.setattr(settings.gemini, "api_key", "env-key")
    await warming.save_settings(
        WarmingSettingsUpdate(
            inter_account_chat=False, reactions_enabled=False, gemini_api_key="ui-key"
        ),
    )
    masked = await warming.save_settings(
        WarmingSettingsUpdate(
            inter_account_chat=False, reactions_enabled=False, clear_gemini_key=True
        ),
    )
    assert masked.has_gemini_key is True  # stored key cleared -> .env fallback present


@pytest.mark.asyncio
async def test_save_settings_persists_openai_key_and_captcha_provider() -> None:
    """The OpenAI captcha key + provider choice persist and surface (masked) on read."""
    masked = await warming.save_settings(
        WarmingSettingsUpdate(
            inter_account_chat=False,
            reactions_enabled=False,
            openai_api_key="sk-ui",
            openai_model="gpt-4o",
            captcha_llm_provider="openai",
        ),
    )
    assert masked.has_openai_key is True
    assert masked.openai_model == "gpt-4o"
    assert masked.captcha_llm_provider == "openai"


@pytest.mark.asyncio
async def test_add_channels_respects_per_add_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "max_channels_per_add", 2)
    raw = "@first_one @second_one @third_one @fourth_one"

    result = await warming.add_channels(AddChannelsRequest(raw=raw))

    assert len(result.channels) == 2


@pytest.mark.asyncio
async def test_add_channels_respects_total_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "max_channels_total", 2)
    await warming.add_channels(AddChannelsRequest(raw="@alpha @beta"))

    result = await warming.add_channels(AddChannelsRequest(raw="@gamma"))

    assert len(result.channels) == 2


@pytest.mark.asyncio
async def test_add_channels_rejects_garbage_tokens() -> None:
    result = await warming.add_channels(
        AddChannelsRequest(raw="!!! a/b/c not-a-channel? @ok_one"),
    )
    assert [ch.channel for ch in result.channels] == ["ok_one"]


@pytest.mark.asyncio
async def test_save_settings_persists_warming_controls() -> None:
    masked = await warming.save_settings(
        WarmingSettingsUpdate(
            inter_account_chat=False,
            reactions_enabled=True,
            enforce_readiness=False,
        ),
    )

    assert masked.enforce_readiness is False

    reloaded = await warming.load_settings()
    assert reloaded.enforce_readiness is False


@pytest.mark.asyncio
async def test_joined_channels_cleanup_on_channel_remove() -> None:
    from sqlalchemy import select  # noqa: PLC0415

    from core.db import (  # noqa: PLC0415
        _warming_joined_channels,
        add_warming_channel,
        record_channel_joined,
    )
    from services.warming.channels import remove_warming_channel  # noqa: PLC0415

    await create_account(AccountCreate(account_id="acc-a"))
    await add_warming_channel("testchan")
    await record_channel_joined("acc-a", "testchan")

    # Verify it exists
    with _get_engine().connect() as conn:
        res = conn.execute(select(_warming_joined_channels)).all()
        assert len(res) == 1

    # Remove channel
    await remove_warming_channel("testchan")

    # Verify cascade delete
    with _get_engine().connect() as conn:
        res = conn.execute(select(_warming_joined_channels)).all()
        assert len(res) == 0


def test_parse_channels_keeps_case_distinct_invite_hashes() -> None:
    """Invite hashes are case-sensitive; usernames are not (#review)."""
    from services.warming.channels import _parse_channels  # noqa: PLC0415

    assert _parse_channels("t.me/+AbCdEfGh12 t.me/+abcdefgh12") == ["+AbCdEfGh12", "+abcdefgh12"]
    assert _parse_channels("@Alpha @alpha") == ["Alpha"]
