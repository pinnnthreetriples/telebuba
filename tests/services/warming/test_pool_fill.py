"""«Найти каналы»: a free account searches, the fits go straight into the pool.

The gateway reads are faked at the warming seam; no real account is touched.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import list_warming_channels
from core.repositories.warming_searches import list_channel_searches_since
from schemas._warming_pool import PoolFillRequest
from schemas.warming import AddChannelsRequest, RemoveChannelRequest
from services import _account_owner, warming
from services.neurocomment import _state as nc_state
from services.neurocomment._state import in_cooldown
from services.warming import _pool_accounts, _seams, pool_fill
from tests.services.warming._pool_support import NOW, FakeTelegram, seed_account, seed_warming

if TYPE_CHECKING:
    from collections.abc import Iterator

_REQUEST = PoolFillRequest(language="es", country="MX", topics=["fútbol"])
BUSY = "chat_broadcast"


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeTelegram]:
    fake = FakeTelegram()
    monkeypatch.setattr(_seams, "execute_read", fake.execute_read)
    monkeypatch.setattr(settings.warming, "pool_fill_pause_seconds", (0.0, 0.0))
    # A flood test cools its account in neurocomment's in-memory cooldowns.
    nc_state.reset_for_tests()
    yield fake
    nc_state.reset_for_tests()


@pytest.mark.asyncio
async def test_every_fit_goes_into_the_pool_at_once(telegram: FakeTelegram) -> None:
    await seed_account("searcher")
    await warming.add_channels(AddChannelsRequest(raw="@cine_mx"))

    result = await pool_fill.find_channels(_REQUEST)

    walked = {c.channel: c for c in (await list_warming_channels()).channels}
    # Too small, too big, a casino, dead and Russian are all left out.
    assert set(walked) == {"cine_mx", "futbol_mx"}
    found = walked["futbol_mx"]
    assert (found.status, found.source, found.language, found.country) == (
        "approved",
        "discovery",
        "es",
        "MX",
    )
    assert found.subscribers == 40_000
    assert (result.status, result.added) == ("ok", 1)
    assert telegram.accounts() == {"searcher"}
    rows = await list_channel_searches_since(NOW - timedelta(days=1))
    assert [(r.origin, r.account_id, r.status, r.added) for r in rows] == [
        ("manual", "searcher", "ok", 1)
    ]


@pytest.mark.asyncio
async def test_a_removed_find_never_comes_back_until_typed_again(telegram: FakeTelegram) -> None:
    del telegram
    await seed_account("searcher")
    await pool_fill.find_channels(_REQUEST)
    await warming.remove_channel(RemoveChannelRequest(channel="futbol_mx"))

    again = await pool_fill.find_channels(_REQUEST)

    assert again.added == 0
    assert [c.channel for c in (await list_warming_channels()).channels] == ["cine_mx"]
    await warming.add_channels(AddChannelsRequest(raw="@Futbol_MX"))
    walked = {c.channel: c.source for c in (await list_warming_channels()).channels}
    assert walked == {"cine_mx": "discovery", "futbol_mx": "manual"}


@pytest.mark.asyncio
async def test_a_warming_account_is_never_searched_with(telegram: FakeTelegram) -> None:
    await seed_warming("warm-1")

    result = await pool_fill.find_channels(_REQUEST)
    pulled = await pool_fill.find_channels(
        _REQUEST.model_copy(update={"pull_account_id": "warm-1"})
    )

    assert result.status == "no_search_account"
    assert pulled.status == "no_search_account"
    assert telegram.reads == []


@pytest.mark.asyncio
async def test_a_busy_account_is_only_offered_never_taken(telegram: FakeTelegram) -> None:
    await seed_account("caster")
    assert _account_owner.try_claim("caster", "chat_broadcast", "camp-1") is None

    result = await pool_fill.find_channels(_REQUEST)

    assert result.status == "needs_consent"
    assert result.pull_candidate is not None
    assert (result.pull_candidate.account_id, result.pull_candidate.busy_with) == (
        "caster",
        "chat_broadcast",
    )
    assert telegram.reads == []
    assert not _account_owner.is_lent("caster")


@pytest.mark.asyncio
async def test_consent_pulls_the_account_and_gives_it_back(telegram: FakeTelegram) -> None:
    await seed_account("caster")
    assert _account_owner.try_claim("caster", "chat_broadcast", "camp-1") is None
    seen: list[tuple[bool, object]] = []

    async def during(account_id: str) -> None:
        # While the search reads, the account is lent and nobody else may claim it.
        seen.append(
            (
                _account_owner.is_lent(account_id),
                _account_owner.try_claim(account_id, "neuroshilling", "x"),
            )
        )

    telegram.during = during
    result = await pool_fill.find_channels(
        _REQUEST.model_copy(update={"pull_account_id": "caster", "pull_busy_with": BUSY})
    )

    assert result.status == "ok"
    assert seen
    assert all(item == (True, "channel_search") for item in seen)
    # Back with its campaign, which never lost its claim.
    assert not _account_owner.is_lent("caster")
    assert (_account_owner.owner_of("caster"), _account_owner.holder_of("caster")) == (
        "chat_broadcast",
        "camp-1",
    )


@pytest.mark.asyncio
async def test_a_failing_search_still_gives_the_account_back(telegram: FakeTelegram) -> None:
    await seed_account("caster")
    assert _account_owner.try_claim("caster", "chat_broadcast", "camp-1") is None

    async def boom(_account_id: str) -> None:
        msg = "gateway crashed"
        raise RuntimeError(msg)

    telegram.during = boom
    with pytest.raises(RuntimeError):
        await pool_fill.find_channels(
            _REQUEST.model_copy(update={"pull_account_id": "caster", "pull_busy_with": BUSY})
        )

    assert not _account_owner.is_lent("caster")
    assert _account_owner.owner_of("caster") == "chat_broadcast"
    assert not pool_fill.is_running()


@pytest.mark.asyncio
async def test_a_cancelled_search_still_gives_the_account_back(telegram: FakeTelegram) -> None:
    await seed_account("searcher")
    started = asyncio.Event()

    async def hang(_account_id: str) -> None:
        started.set()
        await asyncio.Event().wait()

    telegram.during = hang
    task = asyncio.create_task(pool_fill.find_channels(_REQUEST))
    await started.wait()
    assert _account_owner.is_lent("searcher")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not _account_owner.is_lent("searcher")
    assert not pool_fill.is_running()


@pytest.mark.asyncio
async def test_a_search_out_of_time_gives_the_account_back(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_account("searcher")
    monkeypatch.setattr(settings.warming, "pool_fill_timeout_seconds", 0.01)

    async def hang(_account_id: str) -> None:
        await asyncio.Event().wait()

    telegram.during = hang
    result = await pool_fill.find_channels(_REQUEST)

    assert result.status == "failed"
    assert not _account_owner.is_lent("searcher")


@pytest.mark.asyncio
async def test_a_flood_ends_the_search_and_cools_the_account(telegram: FakeTelegram) -> None:
    await seed_account("searcher")
    telegram.flood_on = "get_linked_discussion_group"

    result = await pool_fill.find_channels(_REQUEST)

    # One keyword search, the similar channels, then the first probe hit the limit.
    assert telegram.kinds() == [
        "search_channels",
        "get_similar_channels",
        "get_similar_channels",
        "get_linked_discussion_group",
    ]
    assert result.status == "flooded"
    assert in_cooldown("searcher", NOW)
    rows = await list_channel_searches_since(NOW - timedelta(days=1))
    assert [r.status for r in rows] == ["flood"]


@pytest.mark.asyncio
async def test_only_one_pool_search_runs_at_a_time(telegram: FakeTelegram) -> None:
    await seed_account("searcher")
    await seed_account("second")
    release = asyncio.Event()
    started = asyncio.Event()

    async def hold(_account_id: str) -> None:
        started.set()
        await release.wait()

    telegram.during = hold
    first = asyncio.create_task(pool_fill.find_channels(_REQUEST))
    await started.wait()

    second = await pool_fill.find_channels(_REQUEST)
    release.set()
    await first

    assert second.status == "already_running"
    # Only the first search read, with one account.
    assert len(telegram.accounts()) == 1


@pytest.mark.asyncio
async def test_an_account_claimed_after_the_pick_is_not_searched_with(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_account("searcher")

    async def stale_pick() -> list[str]:
        # The pick said free; a campaign claimed it before the lock was taken.
        assert _account_owner.try_claim("searcher", "masstagging", "camp-1") is None
        return ["searcher"]

    monkeypatch.setattr(pool_fill, "free_search_accounts", stale_pick)
    result = await pool_fill.find_channels(_REQUEST)

    assert result.status == "needs_consent"
    assert telegram.reads == []


@pytest.mark.asyncio
async def test_no_topics_and_no_known_interests_means_no_search(telegram: FakeTelegram) -> None:
    await seed_account("searcher")

    result = await pool_fill.find_channels(PoolFillRequest(language="xx"))

    assert result.status == "no_topics"
    assert telegram.reads == []


@pytest.mark.asyncio
async def test_consent_for_other_work_is_asked_again(telegram: FakeTelegram) -> None:
    await seed_account("caster")
    assert _account_owner.try_claim("caster", "neuroshilling", "camp-1") is None

    # The operator agreed while it was broadcasting; it now runs a neuroshilling dialogue.
    result = await pool_fill.find_channels(
        _REQUEST.model_copy(update={"pull_account_id": "caster", "pull_busy_with": BUSY})
    )

    assert result.status == "needs_consent"
    assert result.pull_candidate is not None
    assert result.pull_candidate.busy_with == "neuroshilling"
    assert telegram.reads == []


@pytest.mark.asyncio
async def test_a_bulk_run_account_a_restart_has_not_resumed_needs_consent(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Only the run's rows name it: the claim comes when the resumed loop starts.
    await seed_account("sender")

    async def bulk() -> set[str]:
        return {"sender"}

    monkeypatch.setattr(_pool_accounts, "_bulk_accounts", bulk)
    result = await pool_fill.find_channels(_REQUEST)

    assert result.status == "needs_consent"
    assert result.pull_candidate is not None
    assert result.pull_candidate.busy_with == "bulk_messages"
    assert telegram.reads == []


@pytest.mark.asyncio
async def test_a_crashed_search_still_rests_the_language(telegram: FakeTelegram) -> None:
    await seed_account("searcher")

    async def boom(_account_id: str) -> None:
        msg = "gateway crashed"
        raise RuntimeError(msg)

    telegram.during = boom
    with pytest.raises(RuntimeError):
        await pool_fill.find_channels(_REQUEST)

    rows = await list_channel_searches_since(NOW - timedelta(days=1))
    assert [r.status for r in rows] == ["failed"]
