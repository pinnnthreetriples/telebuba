"""Discovery stage 1 — the operator's search-time filters: kind, access, seen, limit.

Its own module because ``test_discovery_search`` sits on the 700-line test cap. Every
filter here is decided from what the search hit already carries; the probe-time ones
live in ``test_discovery_qualify_filters``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.repositories.neurocomment import (
    list_discovery_candidates,
    mark_seen,
    replace_discovery_candidates,
)
from schemas.neurocomment_discovery import DiscoveryCandidateRow
from schemas.telegram_actions_discovery import TelegramChannelMatch, TelegramChannelMatches
from services.neurocomment import _seams
from services.neurocomment._discovery_categories import BUNDLES
from services.neurocomment._discovery_pool import AccountPool, SearchAccount
from services.neurocomment._discovery_search import run_search
from tests.services.neurocomment.discovery_support import (
    LISTENER_ID,
    ReadRecorder,
    matches,
    new_campaign,
    pool_of,
    search_request,
    work_for,
)

pytestmark = pytest.mark.usefixtures("isolate_discovery")

_MIXED = TelegramChannelMatches(
    items=[
        TelegramChannelMatch(username="bcast", title="Broadcast", kind="channel"),
        TelegramChannelMatch(username="chat", title="Chat", kind="group"),
    ],
)
_PRIVATE = TelegramChannelMatches(
    items=[
        TelegramChannelMatch(username="pub", title="Public"),
        TelegramChannelMatch(channel_id=123456, title="Private"),
    ],
)


async def _stored(campaign_id: str) -> list[str]:
    return [row.channel for row in (await list_discovery_candidates(campaign_id)).rows]


def _premium_pool_of(*account_ids: str) -> AccountPool:
    """A pool whose accounts are all Premium — the post wave refuses to run without one."""
    return AccountPool(SearchAccount(aid, premium=True) for aid in account_ids or (LISTENER_ID,))


@pytest.mark.asyncio
async def test_hide_seen_drops_what_an_earlier_run_showed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(search=matches(("alpha", "A", None), ("beta", "B", None))),
    )
    await mark_seen(["alpha"], datetime.now(UTC))
    campaign_id = await new_campaign()

    stage = await run_search(campaign_id, pool_of(), search_request(), work_for(pool_of()))

    assert await _stored(campaign_id) == ["beta"]
    assert stage.report.filtered == {"seen": 1}


@pytest.mark.asyncio
async def test_hide_seen_off_keeps_the_familiar_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(search=matches(("alpha", "A", None), ("beta", "B", None))),
    )
    await mark_seen(["alpha"], datetime.now(UTC))
    campaign_id = await new_campaign()

    stage = await run_search(
        campaign_id, pool_of(), search_request(hide_seen=False), work_for(pool_of())
    )

    assert await _stored(campaign_id) == ["alpha", "beta"]
    assert stage.report.filtered == {}


@pytest.mark.asyncio
async def test_hide_seen_folds_case(monkeypatch: pytest.MonkeyPatch) -> None:
    """Usernames are case-insensitive; the seen set must be too, like every other identity."""
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches(("Alpha", "A", None))))
    await mark_seen(["alpha"], datetime.now(UTC))
    campaign_id = await new_campaign()

    stage = await run_search(campaign_id, pool_of(), search_request(), work_for(pool_of()))

    assert stage.report.filtered == {"seen": 1}


@pytest.mark.asyncio
async def test_a_rerun_that_finds_only_seen_channels_keeps_the_previous_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing new to show is not a reason to wipe what was shown.

    The merge came out empty ONLY because every hit was already seen; the sweep answered,
    so the empty result would have replaced the reviewed set with an empty board.
    """
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches(("alpha", "A", None))))
    await mark_seen(["alpha"], datetime.now(UTC))
    campaign_id = await new_campaign()
    await replace_discovery_candidates(
        campaign_id,
        [DiscoveryCandidateRow(channel="alpha", title="A", source="telegram_search")],
    )

    stage = await run_search(campaign_id, pool_of(), search_request(), work_for(pool_of()))

    assert stage.replaced is False
    assert stage.report.stored is False
    assert stage.report.filtered == {"seen": 1}
    assert await _stored(campaign_id) == ["alpha"]


@pytest.mark.asyncio
async def test_kind_is_the_gateways_to_apply_and_each_row_keeps_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every source's action carries ``kind``, so what comes back is already filtered."""
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=_MIXED))
    campaign_id = await new_campaign()

    stage = await run_search(
        campaign_id, pool_of(), search_request(kind="all"), work_for(pool_of())
    )

    assert stage.report.filtered == {}
    # Each stored row remembers which it is, so the board and adopt can tell them apart.
    rows = (await list_discovery_candidates(campaign_id)).rows
    assert {row.channel: row.kind for row in rows} == {"bcast": "channel", "chat": "group"}


@pytest.mark.asyncio
async def test_the_requested_kind_reaches_every_sources_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = ReadRecorder(search=_MIXED)
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await new_campaign()
    pool = _premium_pool_of()

    await run_search(
        campaign_id, pool, search_request(kind="channels", seed_channel="@durov"), work_for(pool)
    )

    assert {action.kind for action in reader.search_actions()} == {"channels"}
    assert {action.kind for action in reader.posts_actions()} == {"channels"}
    assert {action.kind for action in reader.similar_actions()} == {"channels"}


@pytest.mark.asyncio
async def test_the_operators_limit_caps_the_stored_set(monkeypatch: pytest.MonkeyPatch) -> None:
    hits = matches(*((f"chan{index}", "C", None) for index in range(5)))
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=hits))
    campaign_id = await new_campaign()

    stage = await run_search(campaign_id, pool_of(), search_request(limit=2), work_for(pool_of()))

    assert len(await _stored(campaign_id)) == 2
    assert stage.report.capped is True


@pytest.mark.asyncio
async def test_a_private_recommendation_is_stored_under_its_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No handle to normalise, so the id is the row's key — and what makes it unadoptable."""
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(similar=_PRIVATE))
    campaign_id = await new_campaign()

    await run_search(
        campaign_id, pool_of(), search_request(seed_channel="@durov"), work_for(pool_of())
    )

    assert set(await _stored(campaign_id)) == {"pub", "id:123456"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("access", "kept"),
    [("open", ["pub"]), ("join_request", ["pub"]), ("subscription", ["id:123456"])],
)
async def test_access_decides_at_search_time_where_it_can(
    monkeypatch: pytest.MonkeyPatch,
    access: str,
    kept: list[str],
) -> None:
    """Only the handle-less leg is decidable here; open vs join-request waits for the probe."""
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(similar=_PRIVATE))
    campaign_id = await new_campaign()

    stage = await run_search(
        campaign_id,
        pool_of(),
        search_request(seed_channel="@durov", access=access),
        work_for(pool_of()),
    )

    assert await _stored(campaign_id) == kept
    assert stage.report.filtered == {"access": 1}


@pytest.mark.asyncio
async def test_a_category_extends_the_sweep_with_its_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A category alone is a searchable request; typed words come first and are not repeated."""
    reader = ReadRecorder(search=matches())
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await new_campaign()

    await run_search(
        campaign_id,
        pool_of(),
        search_request(keywords=["Crypto"], category="crypto"),
        work_for(pool_of()),
    )

    queries = [action.query for action in reader.search_actions()]
    assert queries == ["Crypto", *(word for word in BUNDLES["crypto"] if word != "crypto")]


@pytest.mark.asyncio
async def test_a_category_alone_searches_its_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = ReadRecorder(search=matches())
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await new_campaign()

    await run_search(
        campaign_id, pool_of(), search_request(keywords=[], category="news"), work_for(pool_of())
    )

    assert [action.query for action in reader.search_actions()] == list(BUNDLES["news"])


@pytest.mark.asyncio
async def test_an_empty_merge_is_all_seen_only_when_seen_was_the_sole_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One hit already seen plus one the access filter refused is an honest empty answer.

    Keeping the previous rows is for the case where NOTHING new could have been shown;
    when another filter also emptied the merge, the old rows are not that answer.
    """
    hits = TelegramChannelMatches(
        items=[
            TelegramChannelMatch(username="alpha", title="A"),
            TelegramChannelMatch(channel_id=777, title="Private"),
        ],
    )
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=hits))
    await mark_seen(["alpha"], datetime.now(UTC))
    campaign_id = await new_campaign()
    await replace_discovery_candidates(
        campaign_id,
        [DiscoveryCandidateRow(channel="alpha", title="A", source="telegram_search")],
    )

    stage = await run_search(
        campaign_id, pool_of(), search_request(access="open"), work_for(pool_of())
    )

    assert stage.all_seen is False
    assert stage.replaced is True
    assert stage.report.filtered == {"seen": 1, "access": 1}
    assert await _stored(campaign_id) == []
