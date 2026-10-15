"""What a found channel must look like, and how a search spends its reads."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.telegram_client import TelegramReadError
from schemas.telegram_actions_discovery import TelegramChannelMatch
from services.warming._pool_filters import is_alive, is_blocked, is_candidate, size_ok
from services.warming._pool_hunt import HuntPlan, hunt_channels
from tests.services.warming._pool_support import FakeTelegram

if TYPE_CHECKING:
    from pydantic import BaseModel

    from schemas.telegram_actions import TelegramReadAction


@pytest.mark.parametrize(
    "text",
    [
        "Apuestas deportivas hoy",
        "КАЗИНО онлайн",
        "Ставки на спорт каждый день",
        "Crypto signals VIP",
        "Сигналы для трейдинга",
        "Contenido 18+",
        "Ganhar dinheiro fácil",
        "1xBet промокоды",
        "Sorteo de un iPhone",
    ],
)
def test_scam_casino_adult_and_signal_titles_are_blocked(text: str) -> None:
    assert is_blocked(text)


@pytest.mark.parametrize(
    "text",
    [
        "Доставки еды в Москве",  # "ставки" only inside a word
        "Pumpkin soup recipes",
        "Alphabet soup",
        "Процентные ставки по вкладам",
        "Recetas de cocina",
    ],
)
def test_a_blocked_word_inside_another_word_does_not_fire(text: str) -> None:
    assert not is_blocked(text)


def test_flagged_private_or_request_only_channels_are_never_candidates() -> None:
    ok = TelegramChannelMatch(username="cocina_mx", title="Recetas de cocina")
    assert is_candidate(ok, "es", set())
    assert not is_candidate(ok, "es", {"cocina_mx"})
    assert not is_candidate(ok.model_copy(update={"flagged": True}), "es", set())
    assert not is_candidate(ok.model_copy(update={"join_request": True}), "es", set())
    assert not is_candidate(TelegramChannelMatch(channel_id=5, title="x"), "es", set())


def test_size_and_age_bounds() -> None:
    now = datetime.now(UTC)
    assert size_ok(2_000)
    assert size_ok(500_000)
    assert not size_ok(1_999)
    assert not size_ok(500_001)
    assert not size_ok(None)
    assert is_alive((now - timedelta(days=13)).isoformat(), now)
    assert not is_alive((now - timedelta(days=15)).isoformat(), now)
    assert not is_alive(None, now)


def _plan(max_reads: int) -> HuntPlan:
    return HuntPlan(
        language="es",
        country="MX",
        topics=("fútbol",),
        source="discovery",
        max_probes=12,
        max_reads=max_reads,
    )


@pytest.mark.asyncio
async def test_a_search_never_reads_past_its_budget() -> None:
    telegram = FakeTelegram()

    async def read(action: TelegramReadAction) -> BaseModel:
        return await telegram.execute_read("acc", action)

    outcome = await hunt_channels(read, _plan(3), set())

    assert len(telegram.reads) == 3
    assert outcome.reads == 3


@pytest.mark.asyncio
async def test_a_failed_read_only_skips_that_read() -> None:

    async def read(action: TelegramReadAction) -> BaseModel:
        kind = getattr(action, "action_type", "")
        if kind == "get_similar_channels":
            reason = "RPC: ChannelPrivateError"
            raise TelegramReadError(reason)
        return await FakeTelegram().execute_read("acc", action)

    outcome = await hunt_channels(read, _plan(50), set())
    assert outcome.failures == 2
    assert [c.channel for c in outcome.fits] == ["futbol_mx"]
    assert outcome.flood is None
