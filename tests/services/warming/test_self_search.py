"""A warming account searching channels for itself, inside its own visit.

Telegram is faked at the warming seam (``execute_owned_read`` for the search,
``execute`` for the visit); no real account is touched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import list_warming_channels
from core.repositories.warming_character import store_account_character
from core.repositories.warming_searches import list_channel_searches_since, record_channel_search
from schemas._warming_character import AccountCharacter
from schemas._warming_pool import ChannelSearchRecord
from schemas.accounts import AccountRead
from schemas.warming import WarmingChannel
from services.warming import _seams
from services.warming._self_search import SelfSearchVisit, search_for_self, walk_found_first
from services.warming._steps import _ChannelTally
from tests.services.warming._pool_support import MX_PHONE, FakeTelegram

if TYPE_CHECKING:
    from schemas.warming import WarmingPhase

_ACCOUNT = AccountRead.model_validate(
    {
        "account_id": "acc-1",
        "status": "alive",
        "phone": MX_PHONE,
        "created_at": "2026-06-12T00:00:00+00:00",
        "updated_at": "2026-06-12T00:00:00+00:00",
    }
)


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> FakeTelegram:
    fake = FakeTelegram()
    monkeypatch.setattr(_seams, "execute_owned_read", fake.owned_read)
    monkeypatch.setattr(settings.warming, "self_search_enabled", True)
    return fake


def _visit(
    *,
    phase: WarmingPhase = "warming",
    kind: str = "normal",
    remaining: int | None = None,
    channels: list[WarmingChannel] | None = None,
) -> SelfSearchVisit:
    return SelfSearchVisit(
        account=_ACCOUNT,
        language="es",
        phase=phase,
        visit_kind=kind,
        channels=channels or [],
        tally=_ChannelTally(),
        remaining_actions=remaining,
    )


async def _searched(origin_hours_ago: float, *, added: int = 1) -> None:
    await record_channel_search(
        ChannelSearchRecord(
            origin="self",
            account_id="acc-1",
            language="es",
            status="ok",
            added=added,
            created_at=(datetime.now(UTC) - timedelta(hours=origin_hours_ago)).isoformat(),
        )
    )


@pytest.mark.asyncio
async def test_it_searches_its_interests_and_its_finds_join_the_pool(
    telegram: FakeTelegram,
) -> None:
    await store_account_character(
        AccountCharacter(
            account_id="acc-1",
            language="es",
            country="MX",
            age=30,
            gender="male",
            occupation="chofer",
            interests=["fútbol", "cocina", "cine"],
        ),
        replace=False,
    )
    visit = _visit()

    stop = await search_for_self(visit)

    assert not stop
    assert telegram.queries[0] in {"fútbol", "cocina", "cine"}
    walked = {c.channel: c for c in (await list_warming_channels()).channels}
    # Three probes: the football channel fits, the tiny and the huge ones do not.
    assert set(walked) == {"futbol_mx"}
    assert {(c.source, c.found_by, c.language, c.country) for c in walked.values()} == {
        ("self_search", "acc-1", "es", "MX")
    }
    # Every read is booked on the visit, and the finds lead its walk.
    assert visit.tally.attempts == len(telegram.reads)
    assert [c.channel for c in visit.found] == ["futbol_mx"]
    rows = await list_channel_searches_since(datetime.now(UTC) - timedelta(days=1))
    assert [(r.origin, r.status, r.added) for r in rows] == [("self", "ok", 1)]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["intro", "settling"])
async def test_the_first_days_never_search(telegram: FakeTelegram, phase: WarmingPhase) -> None:
    assert not await search_for_self(_visit(phase=phase))
    assert telegram.reads == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["glance", "reply"])
async def test_a_short_visit_never_searches(telegram: FakeTelegram, kind: str) -> None:
    assert not await search_for_self(_visit(kind=kind))
    assert telegram.reads == []


@pytest.mark.asyncio
async def test_once_a_day_at_most(telegram: FakeTelegram) -> None:
    await _searched(origin_hours_ago=5)
    assert not await search_for_self(_visit())
    assert telegram.reads == []


@pytest.mark.asyncio
async def test_yesterdays_search_does_not_count_today(telegram: FakeTelegram) -> None:
    await _searched(origin_hours_ago=36)
    await search_for_self(_visit())
    assert telegram.reads


@pytest.mark.asyncio
async def test_three_fruitless_searches_a_week_stop_it(telegram: FakeTelegram) -> None:
    for days in (2, 3, 4):
        await _searched(origin_hours_ago=24 * days, added=0)
    assert not await search_for_self(_visit())
    assert telegram.reads == []


@pytest.mark.asyncio
async def test_it_never_spends_the_reserve(telegram: FakeTelegram) -> None:
    reserve = settings.warming.self_search_reserve_actions
    await search_for_self(_visit(remaining=reserve + 2))
    assert telegram.reads == []

    visit = _visit(remaining=reserve + 4)
    await search_for_self(visit)
    assert 0 < len(telegram.reads) <= 4
    assert visit.tally.attempts <= 4


@pytest.mark.asyncio
async def test_an_account_with_enough_channels_does_not_search(telegram: FakeTelegram) -> None:
    created = "2026-01-01T00:00:00+00:00"
    own = [
        WarmingChannel(channel=f"c{i}", created_at=created, language="es", found_by="acc-1")
        for i in range(10)
    ]
    assert not await search_for_self(_visit(channels=own))
    assert telegram.reads == []


@pytest.mark.asyncio
async def test_a_flood_stops_the_visit_like_a_join_flood(telegram: FakeTelegram) -> None:
    telegram.flood_on = "search_channels"
    telegram.flood_seconds = 300
    visit = _visit()

    stop = await search_for_self(visit)

    assert stop
    assert telegram.kinds() == ["search_channels"]
    assert (visit.tally.flooded, visit.tally.flood_seconds) == (True, 300)
    assert visit.tally.flood_until is not None
    rows = await list_channel_searches_since(datetime.now(UTC) - timedelta(days=1))
    assert [r.status for r in rows] == ["flood"]


@pytest.mark.asyncio
async def test_a_peer_flood_stops_it_too(telegram: FakeTelegram) -> None:
    telegram.flood_on = "search_channels"
    telegram.flood_seconds = None
    visit = _visit()

    assert await search_for_self(visit)
    assert visit.tally.peer_flooded


def test_its_finds_lead_the_walk_without_lengthening_it() -> None:
    created = "2026-01-01T00:00:00+00:00"
    chosen = [WarmingChannel(channel=name, created_at=created) for name in ("a", "b", "c")]
    found = [WarmingChannel(channel=name, created_at=created) for name in ("x", "b")]

    walked = walk_found_first(chosen, found)

    assert [c.channel for c in walked] == ["x", "b", "a"]
    assert walk_found_first(chosen, []) == chosen
