"""Channels by language (Warming 2.0, stage 4.1): affinity, labels, shortages."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import create_account, list_warming_channels, upsert_warming_state
from schemas.accounts import AccountCreate
from schemas.telegram_action_results import ReadChannelFacts
from schemas.telegram_actions import ActionResult
from schemas.warming import (
    AddChannelsRequest,
    UpdateChannelRequest,
    WarmingChannel,
    WarmingIntensity,
    WarmingStateWrite,
)
from services import warming
from services.warming import _cycle, _fleet, _seams, _steps
from services.warming._channel_language import account_language, language_shortages

if TYPE_CHECKING:
    from schemas.telegram_actions import TelegramAction

# Placeholder numbers: only their calling codes matter (Mexico, Russia).
_MX_PHONE = "+525512345678"
_RU_PHONE = "+79161234567"


def _pool(**sizes: int) -> list[WarmingChannel]:
    """``es=2, unknown=1`` → two ``es_*`` channels and one unlabelled ``unknown_*``."""
    return [
        WarmingChannel(
            channel=f"{lang}_{i}",
            created_at="2026-01-01T00:00:00+00:00",
            language=None if lang == "unknown" else lang,
        )
        for lang, size in sizes.items()
        for i in range(size)
    ]


@pytest.fixture(autouse=True)
def _affinity_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "channels_per_cycle_min", 1)
    monkeypatch.setattr(settings.warming, "channel_affinity_min", 3)
    monkeypatch.setattr(settings.warming, "channel_affinity_ratio", 0.25)


def _languages(channels: list[WarmingChannel]) -> list[str | None]:
    return [c.language for c in channels]


def test_account_language_follows_the_phone_and_is_unknown_without_one() -> None:
    assert account_language(_MX_PHONE) == "es"
    assert account_language(_RU_PHONE) == "ru"
    assert account_language(None) is None


def test_affinity_picks_only_own_language_when_there_are_enough() -> None:
    affinity = _fleet._account_channel_affinity("acc-1", _pool(es=6, ru=6, unknown=3), 0, "es")

    assert len(affinity) == 3
    assert set(_languages(affinity)) == {"es"}


def test_affinity_never_drops_below_the_floor_and_puts_own_language_first() -> None:
    affinity = _fleet._account_channel_affinity("acc-1", _pool(es=1, unknown=1, ru=10), 0, "es")

    assert len(affinity) == 3
    assert _languages(affinity)[:2] == ["es", None]
    assert _languages(affinity)[2] == "ru"


def test_unlabelled_channels_are_the_fallback_when_nothing_matches() -> None:
    affinity = _fleet._account_channel_affinity("acc-1", _pool(unknown=8, ru=8), 0, "es")

    assert len(affinity) == 3
    assert set(_languages(affinity)) == {None}


def test_an_account_without_a_language_keeps_the_whole_pool() -> None:
    pool = _pool(es=4, ru=8)

    affinity = _fleet._account_channel_affinity("acc-1", pool, 0, None)

    assert len(affinity) == 3  # round(12 * 0.25) = 3, drawn from both languages' channels
    assert {c.channel for c in affinity} <= {c.channel for c in pool}


def test_the_cache_key_carries_the_account_language_and_the_labels() -> None:
    pool = _pool(es=4, ru=4)

    spanish = _fleet._account_channel_affinity("acc-1", pool, 0, "es")
    russian = _fleet._account_channel_affinity("acc-1", pool, 0, "ru")
    relabelled = [c.model_copy(update={"language": "es"}) for c in pool]
    widened = _fleet._account_channel_affinity("acc-1", relabelled, 0, "es")

    assert set(_languages(spanish)) == {"es"}
    assert set(_languages(russian)) == {"ru"}
    assert {key[2] for key in _fleet._AFFINITY_CACHE} == {"es", "ru"}
    assert widened is not spanish  # a relabel is a different key, not a stale hit


def test_exploration_stays_inside_the_language_pool() -> None:
    # The conftest's rng makes every exploration roll pass.
    intensity = WarmingIntensity(
        phase="warming",
        daily_cap=10,
        channels_min=3,
        channels_max=3,
        reaction_probability=0.0,
        dm_allowed=False,
    )

    chosen = _cycle._pick_channels("acc-1", _pool(es=3, ru=10), "es", intensity)

    assert set(_languages(chosen)) == {"es"}


def test_shortages_name_each_language_below_the_floor() -> None:
    phones = [_MX_PHONE] * 7 + [_RU_PHONE] * 2 + [None]
    channels = _pool(es=2, ru=5, unknown=4)

    shortages = language_shortages(phones, channels)

    assert [(s.language, s.channels, s.accounts) for s in shortages] == [("es", 2, 7)]


@pytest.mark.asyncio
async def test_operator_label_wins_over_a_later_read(monkeypatch: pytest.MonkeyPatch) -> None:
    await warming.add_channels(AddChannelsRequest(raw="@alpha\n@beta"))
    await warming.label_channel(UpdateChannelRequest(channel="alpha", language="ru", country="RU"))
    facts = ReadChannelFacts(
        text_sample="Las mejores noticias del día con todo lo que pasa en la ciudad",
        kind="channel",
        last_post_at="2026-10-01T10:00:00+00:00",
    )

    async def _read(account_id: str, action: TelegramAction) -> ActionResult:
        return ActionResult(
            status="ok",
            action_type=action.action_type,
            account_id=account_id,
            read_facts=facts,
        )

    monkeypatch.setattr(_seams, "execute", _read)
    for channel in ("alpha", "beta"):
        await _steps._read_and_react(
            "acc-1",
            channel,
            _steps._ChannelTally(),
            reactions_enabled=False,
            reaction_probability=0.0,
            remaining_actions=None,
        )

    rows = {c.channel: c for c in (await list_warming_channels()).channels}
    assert (rows["alpha"].language, rows["alpha"].country) == ("ru", "RU")
    assert rows["beta"].language == "es"
    assert rows["beta"].kind == "channel"
    assert rows["beta"].last_post_at == "2026-10-01T10:00:00+00:00"
    assert (rows["beta"].source, rows["beta"].status) == ("manual", "approved")


@pytest.mark.asyncio
async def test_labelling_an_unknown_channel_is_refused() -> None:
    with pytest.raises(warming.UnknownChannelError):
        await warming.label_channel(UpdateChannelRequest(channel="ghost", language="es"))


@pytest.mark.asyncio
async def test_the_board_reports_language_shortages() -> None:
    await warming.add_channels(AddChannelsRequest(raw="@alpha\n@beta"))
    await warming.label_channel(UpdateChannelRequest(channel="alpha", language="es"))
    await create_account(AccountCreate(account_id="acc-mx", phone=_MX_PHONE))
    await create_account(AccountCreate(account_id="acc-ru", phone=_RU_PHONE))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-ru", state="active"))

    board = await warming.load_board()

    assert [(s.language, s.channels, s.accounts) for s in board.language_shortages] == [
        ("es", 1, 1),
        ("ru", 0, 1),
    ]
