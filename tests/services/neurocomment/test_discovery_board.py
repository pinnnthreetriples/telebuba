"""Discovery board assembly and bulk adopt."""

from __future__ import annotations

import pytest

from core import db
from core.db import create_campaign, link_channel_to_campaign
from core.repositories.neurocomment import (
    mark_discovery_qualified,
    replace_discovery_candidates,
    upsert_linked_group,
)
from schemas.neurocomment import CampaignCreate
from schemas.neurocomment_discovery import DiscoveryCandidateRow
from services.neurocomment import _discovery_state, _runtime
from services.neurocomment.discovery import adopt_candidates, load_discovery

pytestmark = pytest.mark.usefixtures("isolate_discovery")


def _row(channel: str, *, source: str = "telegram_search") -> DiscoveryCandidateRow:
    return DiscoveryCandidateRow.model_validate(
        {"channel": channel, "title": channel.title(), "source": source},
    )


async def _campaign(name: str = "C") -> str:
    campaign = await create_campaign(CampaignCreate(name=name, prompt="p"))
    return campaign.campaign_id


@pytest.mark.asyncio
async def test_unknown_campaign_returns_none() -> None:
    assert await load_discovery("ghost") is None
    assert await adopt_candidates("ghost", ["alpha"]) is None


@pytest.mark.asyncio
async def test_board_of_a_campaign_that_never_searched_is_idle() -> None:
    campaign_id = await _campaign()

    board = await load_discovery(campaign_id)

    assert board is not None
    assert board.progress.phase == "idle"
    assert board.progress.running is False
    assert board.progress.total == 0
    assert board.candidates == []


@pytest.mark.asyncio
async def test_unprobed_candidates_are_pending() -> None:
    campaign_id = await _campaign()
    await replace_discovery_candidates(campaign_id, [_row("alpha"), _row("beta")])

    board = await load_discovery(campaign_id)

    assert board is not None
    assert [c.qualification for c in board.candidates] == ["pending", "pending"]
    assert board.progress.qualified == 0
    assert board.progress.comments_on == 0


@pytest.mark.asyncio
async def test_qualification_reads_the_shared_linked_group_cache() -> None:
    campaign_id = await _campaign()
    await replace_discovery_candidates(campaign_id, [_row("withcomments"), _row("without")])
    await upsert_linked_group("withcomments", -100, comments_enabled=True)
    await upsert_linked_group("without", None, comments_enabled=False)
    await mark_discovery_qualified(campaign_id, "withcomments")
    await mark_discovery_qualified(campaign_id, "without")

    board = await load_discovery(campaign_id)

    assert board is not None
    by_channel = {c.channel: c for c in board.candidates}
    assert by_channel["withcomments"].qualification == "comments_on"
    assert by_channel["without"].qualification == "comments_off"
    assert board.progress.qualified == 2
    assert board.progress.comments_on == 1


@pytest.mark.asyncio
async def test_a_failed_probe_reads_as_unknown_not_pending() -> None:
    """The operator must be able to tell "wait" from "we could not tell".

    The cache deliberately DISAGREES: with a stale ``comments_enabled=True`` row from an
    earlier run, dropping the error check would report a confident green verdict — and
    count it in ``comments_on`` — for a probe that actually failed. Without the cache
    row the uncached fallback returns "unknown" anyway and the test proves nothing.
    """
    campaign_id = await _campaign()
    await replace_discovery_candidates(campaign_id, [_row("broken")])
    await upsert_linked_group("broken", -100, comments_enabled=True)
    await mark_discovery_qualified(campaign_id, "broken", error="RPC: ChannelPrivateError")

    board = await load_discovery(campaign_id)

    assert board is not None
    assert board.candidates[0].qualification == "unknown"
    assert board.progress.comments_on == 0
    assert board.progress.qualified == 1
    assert board.progress.comments_on == 0


@pytest.mark.asyncio
async def test_probed_but_uncached_reads_as_unknown() -> None:
    campaign_id = await _campaign()
    await replace_discovery_candidates(campaign_id, [_row("orphan")])
    await mark_discovery_qualified(campaign_id, "orphan")

    board = await load_discovery(campaign_id)

    assert board is not None
    assert board.candidates[0].qualification == "unknown"


@pytest.mark.asyncio
async def test_membership_flags_distinguish_this_campaign_from_another() -> None:
    mine = await _campaign("Mine")
    theirs = await _campaign("Theirs")
    await replace_discovery_candidates(mine, [_row("ours"), _row("theirs"), _row("free")])
    await link_channel_to_campaign(mine, "ours")
    await link_channel_to_campaign(theirs, "theirs")

    board = await load_discovery(mine)

    assert board is not None
    by_channel = {c.channel: c for c in board.candidates}
    assert by_channel["ours"].in_campaign is True
    assert by_channel["ours"].taken_by_other_campaign is False
    assert by_channel["theirs"].in_campaign is False
    assert by_channel["theirs"].taken_by_other_campaign is True
    assert by_channel["free"].in_campaign is False
    assert by_channel["free"].taken_by_other_campaign is False


@pytest.mark.asyncio
async def test_board_reports_phase_and_error_from_run_state() -> None:
    campaign_id = await _campaign()
    _discovery_state.set_phase(campaign_id, "failed")
    _discovery_state.set_last_error(campaign_id, "FloodWait(120s)")

    board = await load_discovery(campaign_id)

    assert board is not None
    assert board.progress.phase == "failed"
    assert board.progress.last_error == "FloodWait(120s)"


@pytest.mark.asyncio
async def test_adopt_links_every_pick() -> None:
    campaign_id = await _campaign()

    result = await adopt_candidates(campaign_id, ["alpha", "beta"])

    assert result is not None
    assert [outcome.status for outcome in result.outcomes] == ["linked", "linked"]
    from core.db import list_campaign_channels  # noqa: PLC0415

    links = await list_campaign_channels(campaign_id)
    assert sorted(link.channel for link in links.links) == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_adopt_reports_a_taken_channel_as_a_status_not_an_exception() -> None:
    mine = await _campaign("Mine")
    theirs = await _campaign("Theirs")
    await link_channel_to_campaign(theirs, "taken")

    result = await adopt_candidates(mine, ["free", "taken"])

    assert result is not None
    statuses = {outcome.channel: outcome.status for outcome in result.outcomes}
    assert statuses == {"free": "linked", "taken": "already_assigned"}


@pytest.mark.asyncio
async def test_adopt_reconciles_the_listener_once_for_many_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """30 adopted channels must not trigger 30 listener reconciles."""
    calls: list[int] = []

    async def _record() -> None:
        calls.append(1)

    monkeypatch.setattr(_runtime, "reconcile_if_running", _record)
    campaign_id = await _campaign()

    await adopt_candidates(campaign_id, [f"chan_{index}" for index in range(5)])

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_adopt_skips_the_reconcile_when_nothing_was_linked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    async def _record() -> None:
        calls.append(1)

    monkeypatch.setattr(_runtime, "reconcile_if_running", _record)
    mine = await _campaign("Mine")
    theirs = await _campaign("Theirs")
    await link_channel_to_campaign(theirs, "taken")

    result = await adopt_candidates(mine, ["taken"])

    assert result is not None
    assert [outcome.status for outcome in result.outcomes] == ["already_assigned"]
    assert calls == []


@pytest.mark.asyncio
async def test_a_failure_mid_batch_still_reconciles_what_linked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Otherwise a running listener ignores those channels until the next restart."""
    calls: list[int] = []

    async def _record() -> None:
        calls.append(1)

    async def _fail_on_second(campaign_id: str, channel: str) -> None:
        if channel == "second":
            raise RuntimeError
        await link_channel_to_campaign(campaign_id, channel)

    monkeypatch.setattr(_runtime, "reconcile_if_running", _record)
    monkeypatch.setattr(db, "link_channel_to_campaign", _fail_on_second)
    campaign_id = await _campaign()

    with pytest.raises(RuntimeError):
        await adopt_candidates(campaign_id, ["first", "second", "third"])

    assert len(calls) == 1
