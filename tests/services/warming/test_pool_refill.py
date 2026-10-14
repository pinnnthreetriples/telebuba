"""The automatic refill: a short language is searched on its own, never in a loop.

Telegram is faked at the warming seam; no real account is touched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import list_warming_channels
from core.repositories.warming_character import store_account_character
from core.repositories.warming_searches import list_channel_searches_since
from schemas._warming_character import AccountCharacter
from schemas._warming_pool import PoolFillRequest
from services import _account_owner
from services.neurocomment import _state as nc_state
from services.warming import _seams, pool_fill, pool_refill
from services.warming._pool_requests import pending_refill_request
from tests.services.warming._pool_support import MX_PHONE, FakeTelegram, seed_account

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeTelegram]:
    fake = FakeTelegram()
    monkeypatch.setattr(_seams, "execute_read", fake.execute_read)
    monkeypatch.setattr(settings.warming, "pool_fill_pause_seconds", (0.0, 0.0))
    monkeypatch.setattr(settings.warming, "pool_refill_enabled", True)
    nc_state.reset_for_tests()
    yield fake
    nc_state.reset_for_tests()


async def _rows() -> list[tuple[str, str]]:
    since = datetime.now(UTC) - timedelta(days=1)
    return [(r.origin, r.status) for r in await list_channel_searches_since(since)]


@pytest.mark.asyncio
async def test_a_short_language_is_searched_on_its_accounts_interests(
    telegram: FakeTelegram,
) -> None:
    # A Mexican account to warm: Spanish is short (no channel at all yet).
    await seed_account("reader", phone=MX_PHONE)
    await seed_account("searcher")
    await store_account_character(
        AccountCharacter(
            account_id="reader",
            language="es",
            country="MX",
            age=30,
            gender="female",
            occupation="maestra",
            interests=["fútbol", "jardinería", "series"],
        ),
        replace=False,
    )

    language = await pool_refill.run_refill_pass()

    assert language == "es"
    assert set(telegram.queries) == {"fútbol", "jardinería", "series", "noticias", "recetas"}
    # Neither account is in warming, so either may read; one does.
    assert len(telegram.accounts()) == 1
    assert "futbol_mx" in {c.channel for c in (await list_warming_channels()).channels}
    assert await _rows() == [("refill", "ok")]


@pytest.mark.asyncio
async def test_a_language_is_searched_once_per_interval(telegram: FakeTelegram) -> None:
    await seed_account("reader", phone=MX_PHONE)
    await seed_account("searcher")
    await pool_refill.run_refill_pass()
    reads = len(telegram.reads)

    # Still short (one find is below the floor), but searched minutes ago.
    assert await pool_refill.run_refill_pass() is None
    assert len(telegram.reads) == reads

    later = datetime.now(UTC) + timedelta(hours=settings.warming.pool_refill_interval_hours + 1)
    assert await pool_refill.run_refill_pass(now=later) == "es"


@pytest.mark.asyncio
async def test_with_no_free_account_it_asks_once_and_takes_nothing(
    telegram: FakeTelegram,
) -> None:
    await seed_account("reader", phone=MX_PHONE)
    await seed_account("caster")
    assert _account_owner.try_claim("caster", "chat_broadcast", "camp-1") is None
    assert _account_owner.try_claim("reader", "masstagging", "camp-2") is None

    await pool_refill.run_refill_pass()
    await pool_refill.run_refill_pass()

    assert telegram.reads == []
    assert await _rows() == [("refill", "no_account")]
    request = await pending_refill_request(["es"])
    assert request is not None
    assert request.language == "es"

    # The operator agrees to pull one: the search runs and the request is answered.
    pulled = await pool_fill.find_channels(
        PoolFillRequest(language="es", pull_account_id="caster", pull_busy_with="chat_broadcast")
    )
    assert pulled.status == "ok"
    assert await pending_refill_request(["es"]) is None
    assert _account_owner.owner_of("caster") == "chat_broadcast"
    assert not _account_owner.is_lent("caster")


@pytest.mark.asyncio
async def test_a_free_account_answers_the_request_on_the_next_pass(
    telegram: FakeTelegram,
) -> None:
    await seed_account("reader", phone=MX_PHONE)
    assert _account_owner.try_claim("reader", "masstagging", "camp-2") is None
    await pool_refill.run_refill_pass()
    assert telegram.reads == []

    await seed_account("searcher")
    assert await pool_refill.run_refill_pass() == "es"
    assert telegram.accounts() == {"searcher"}
    assert await pending_refill_request(["es"]) is None


@pytest.mark.asyncio
async def test_switched_off_or_busy_it_does_nothing(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_account("reader", phone=MX_PHONE)
    await seed_account("searcher")
    monkeypatch.setattr(pool_fill, "is_running", lambda: True)
    assert await pool_refill.run_refill_pass() is None
    monkeypatch.setattr(pool_fill, "is_running", lambda: False)
    monkeypatch.setattr(settings.warming, "pool_refill_enabled", False)
    assert await pool_refill.run_refill_pass() is None
    assert await pending_refill_request(["es"]) is None
    assert telegram.reads == []


@pytest.mark.asyncio
async def test_the_loop_starts_and_stops_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "pool_refill_enabled", True)
    pool_refill.reset_for_tests()
    await pool_refill.start_pool_refill()
    await pool_refill.start_pool_refill()
    await pool_refill.shutdown_pool_refill()
    await pool_refill.shutdown_pool_refill()
