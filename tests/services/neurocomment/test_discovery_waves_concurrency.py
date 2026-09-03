"""Discovery stage 1 — several accounts genuinely sharing one wave.

Split from ``test_discovery_waves.py`` (700-line test cap). ``ReadRecorder`` never
really suspends, so under it whichever account's stream starts first drains every job
it is eligible for before its peer gets a look-in — fine for a total-reads assertion,
but it would hide a bug in the one thing these tests exist to prove: that several
accounts genuinely interleave. Each reader below awaits a real ``asyncio.sleep(0)``,
the same technique ``test_discovery_streams.py`` uses, so the streams actually overlap.
"""

from __future__ import annotations

import asyncio

import pytest

from core.telegram_client import TelegramReadError
from schemas.telegram_actions import GetSimilarChannels, SearchChannels, SearchGlobalPosts
from schemas.telegram_actions_discovery import GlobalPostsCursor
from services.neurocomment import _seams
from services.neurocomment._discovery_waves import _GLOBAL_MAX_READS, native_pass
from tests.services.neurocomment.discovery_support import (
    matches,
    pool_of,
    posts_page,
    search_request,
    work_for,
)

pytestmark = pytest.mark.usefixtures("isolate_discovery")

_RICH_SWEEP = matches(*((f"chan{index}", "C", 900 - index) for index in range(6)))


def _keywords(count: int) -> list[str]:
    return [f"word{index:02d}" for index in range(count)]


@pytest.mark.asyncio
async def test_two_accounts_share_the_keyword_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two keywords, two accounts: both stream something, not one account doing both."""
    seen: list[str] = []

    async def reader(account_id: str, _action: object) -> object:
        seen.append(account_id)
        await asyncio.sleep(0)
        return matches()

    monkeypatch.setattr(_seams, "execute_read", reader)
    pool = pool_of("acc-a", "acc-b")

    wave = await native_pass(
        pool, search_request(keywords=_keywords(2), kind="groups"), work_for(pool)
    )

    assert set(seen) == {"acc-a", "acc-b"}
    assert wave.stop is None


@pytest.mark.asyncio
async def test_one_account_floods_mid_sweep_and_the_other_finishes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flooded keyword earns one retry on whichever account picks it up next.

    "acc-a" is dropped the moment it floods, so the retry — and every keyword "acc-a"
    never got to — lands on "acc-b". The retry succeeds, so the source reports itself
    ``ran`` with no error: the flood is visible only as "acc-a"'s own cooldown.
    """
    flooded_once = False
    calls: list[str] = []

    async def reader(account_id: str, action: object) -> object:
        nonlocal flooded_once
        await asyncio.sleep(0)
        if not isinstance(action, SearchChannels):
            return matches()
        calls.append(account_id)
        if account_id == "acc-a" and not flooded_once:
            flooded_once = True
            reason = "FloodWait(60s)"
            raise TelegramReadError(reason, kind="flood_wait", seconds=60)
        return matches()

    monkeypatch.setattr(_seams, "execute_read", reader)
    pool = pool_of("acc-a", "acc-b")

    wave = await native_pass(
        pool, search_request(keywords=_keywords(4), kind="groups"), work_for(pool)
    )

    assert wave.stop is None
    # "acc-a" is dropped after its one (flooded) attempt; the retry plus the three
    # keywords it never reached all land on "acc-b" — five reads for four keywords.
    assert calls.count("acc-a") == 1
    assert len(calls) == 5
    search_outcomes = [outcome for outcome in wave.outcomes if outcome.source == "telegram_search"]
    assert len(search_outcomes) == 4
    assert all(outcome.state == "ran" and outcome.error is None for outcome in search_outcomes)


@pytest.mark.asyncio
async def test_every_account_flooding_stops_the_wave(monkeypatch: pytest.MonkeyPatch) -> None:
    async def reader(_account_id: str, _action: object) -> object:
        await asyncio.sleep(0)
        reason = "FloodWait(60s)"
        raise TelegramReadError(reason, kind="flood_wait", seconds=60)

    monkeypatch.setattr(_seams, "execute_read", reader)
    pool = pool_of("acc-a", "acc-b")

    wave = await native_pass(
        pool, search_request(keywords=_keywords(4), kind="groups"), work_for(pool)
    )

    assert wave.stop == "flooded"
    assert pool.empty is True


@pytest.mark.asyncio
async def test_recommendations_wait_for_the_whole_sweep_to_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``getChannelRecommendations`` may arrive before every keyword has answered.

    The recommendation wave is seeded from the WHOLE sweep, not whatever happened to
    finish first.
    """
    finished_keywords = 0
    total_keywords = 3
    early_recommendations: list[int] = []

    async def reader(_account_id: str, action: object) -> object:
        nonlocal finished_keywords
        await asyncio.sleep(0)
        if isinstance(action, GetSimilarChannels):
            if finished_keywords < total_keywords:
                early_recommendations.append(finished_keywords)
            return matches()
        if isinstance(action, SearchChannels):
            finished_keywords += 1
            return _RICH_SWEEP
        return matches()

    monkeypatch.setattr(_seams, "execute_read", reader)
    pool = pool_of("acc-a", "acc-b", "acc-c")

    wave = await native_pass(
        pool, search_request(keywords=_keywords(total_keywords)), work_for(pool)
    )

    assert early_recommendations == []
    assert wave.stop is None


@pytest.mark.asyncio
async def test_post_pages_never_exceed_the_waves_ceiling_with_three_accounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every page offers a fresh channel and another cursor, trying to overrun the cap."""
    post_calls = 0

    async def reader(_account_id: str, action: object) -> object:
        nonlocal post_calls
        await asyncio.sleep(0)
        if isinstance(action, SearchGlobalPosts):
            post_calls += 1
            cursor = GlobalPostsCursor(offset_rate=post_calls, peer="p")
            return posts_page((f"post{post_calls}", "P", None), cursor=cursor)
        return matches()

    monkeypatch.setattr(_seams, "execute_read", reader)
    pool = pool_of("acc-a", "acc-b", "acc-c")

    await native_pass(pool, search_request(keywords=_keywords(3), kind="groups"), work_for(pool))

    assert post_calls <= _GLOBAL_MAX_READS
