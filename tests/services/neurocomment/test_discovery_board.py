"""Discovery board assembly and bulk adopt."""

from __future__ import annotations

import asyncio

import pytest

from core import db
from core.config import settings
from core.db import create_campaign, link_channel_to_campaign
from core.repositories.neurocomment import (
    mark_discovery_qualified,
    replace_discovery_candidates,
    upsert_linked_group,
)
from schemas.neurocomment import CampaignCreate
from schemas.neurocomment_discovery import (
    DiscoveryCandidateOrigin,
    DiscoveryCandidateRow,
    DiscoveryChannelVerdict,
    DiscoveryRunReport,
    DiscoverySourceReport,
)
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
async def test_board_reports_per_source_outcomes_and_provenance() -> None:
    """The one addition that turns a source which never answered from silent into obvious."""
    campaign_id = await _campaign()
    await replace_discovery_candidates(campaign_id, [_row("shared", source="telegram_search")])
    _discovery_state.set_run_report(
        campaign_id,
        DiscoveryRunReport(
            sources=[
                DiscoverySourceReport(source="telegram_search", state="ran", hits=3, kept=1),
                DiscoverySourceReport(
                    source="telegram_similar",
                    state="skipped",
                    reason="seed_unusable",
                ),
            ],
            origins={
                "shared": DiscoveryCandidateOrigin(
                    sources=["telegram_search", "telegram_similar"],
                ),
            },
        ),
    )

    board = await load_discovery(campaign_id)

    assert board is not None
    assert [(item.source, item.state) for item in board.progress.sources] == [
        ("telegram_search", "ran"),
        ("telegram_similar", "skipped"),
    ]
    assert board.candidates[0].sources == ["telegram_search", "telegram_similar"]


@pytest.mark.asyncio
async def test_a_candidate_with_no_run_state_falls_back_to_its_stored_source() -> None:
    """Multi-source provenance is not persisted, so a restart loses that only."""
    campaign_id = await _campaign()
    await replace_discovery_candidates(campaign_id, [_row("orphan", source="telegram_similar")])

    board = await load_discovery(campaign_id)

    assert board is not None
    assert board.candidates[0].sources == ["telegram_similar"]
    assert board.progress.sources == []


@pytest.mark.asyncio
async def test_the_board_carries_the_fitness_verdict_of_the_run_in_flight() -> None:
    """Comments-on alone never told the operator WHY a channel is a dead end."""
    campaign_id = await _campaign()
    await replace_discovery_candidates(campaign_id, [_row("gated")])
    await upsert_linked_group("gated", -100, comments_enabled=True)
    await mark_discovery_qualified(campaign_id, "gated")
    _discovery_state.record_verdict(
        campaign_id,
        "gated",
        DiscoveryChannelVerdict(join_request=True, broadcast_slowmode_seconds=60),
    )

    board = await load_discovery(campaign_id)

    assert board is not None
    verdict = board.candidates[0].verdict
    assert verdict is not None
    assert verdict.join_request is True
    assert verdict.broadcast_slowmode_seconds == 60
    # Not measured by this run, and it must not read as a "no".
    assert verdict.can_send_messages is None


@pytest.mark.asyncio
async def test_a_candidate_with_no_recorded_verdict_reads_as_unknown_not_fine() -> None:
    """The verdict is not persisted, so a board read after a restart has none.

    ``None`` is the only honest answer there — degrading to an empty (all-``False``)
    verdict would advertise every gate as cleared for a channel nobody measured.
    """
    campaign_id = await _campaign()
    await replace_discovery_candidates(campaign_id, [_row("orphan")])
    await upsert_linked_group("orphan", -100, comments_enabled=True)
    await mark_discovery_qualified(campaign_id, "orphan")

    board = await load_discovery(campaign_id)

    assert board is not None
    assert board.candidates[0].qualification == "comments_on"
    assert board.candidates[0].verdict is None


@pytest.mark.asyncio
async def test_adopt_refuses_a_channel_whose_cached_verdict_says_comments_are_off() -> None:
    """The UI disabling the checkbox was the ONLY enforcement; a direct caller bypassed it.

    A per-channel status, not an exception: the other picks must still link and still be
    reported.
    """
    campaign_id = await _campaign()
    await upsert_linked_group("silent", None, comments_enabled=False)
    await upsert_linked_group("talkative", -100, comments_enabled=True)

    result = await adopt_candidates(campaign_id, ["silent", "talkative"])

    assert result is not None
    assert [(o.channel, o.status) for o in result.outcomes] == [
        ("silent", "comments_off"),
        ("talkative", "linked"),
    ]
    from core.db import list_campaign_channels  # noqa: PLC0415

    links = await list_campaign_channels(campaign_id)
    assert [link.channel for link in links.links] == ["talkative"]


@pytest.mark.asyncio
async def test_adopt_does_not_refuse_a_channel_that_was_never_probed() -> None:
    """A cold cache must not become a way to block adoption."""
    campaign_id = await _campaign()

    result = await adopt_candidates(campaign_id, ["unprobed"])

    assert result is not None
    assert [o.status for o in result.outcomes] == ["linked"]


@pytest.mark.asyncio
async def test_adopt_does_not_refuse_on_a_verdict_probed_long_ago(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A channel that switched comments on since the probe must still be adoptable.

    The guard shares ``_discovery_qualify._is_fresh`` with the probe loop, whose own
    tests pin the unit of the TTL; zeroing it here is the shortest way to say "every
    cached verdict is past its window".
    """
    monkeypatch.setattr(settings.neurocomment, "discovery_linked_group_ttl_hours", 0)
    campaign_id = await _campaign()
    await upsert_linked_group("reopened", None, comments_enabled=False)

    result = await adopt_candidates(campaign_id, ["reopened"])

    assert result is not None
    assert [o.status for o in result.outcomes] == ["linked"]


@pytest.mark.asyncio
async def test_a_batch_of_only_refusals_links_nothing_and_skips_the_reconcile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    async def _record() -> None:
        calls.append(1)

    monkeypatch.setattr(_runtime, "reconcile_if_running", _record)
    campaign_id = await _campaign()
    await upsert_linked_group("silent", None, comments_enabled=False)

    result = await adopt_candidates(campaign_id, ["silent"])

    assert result is not None
    assert [o.status for o in result.outcomes] == ["comments_off"]
    assert calls == []


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
async def test_a_failure_mid_batch_is_reported_per_channel_and_reconciles_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The report is the point: aborting hid the channels that had already linked.

    And the reconcile still fires exactly once — a running listener would otherwise
    ignore the linked channels until the next restart.
    """
    calls: list[int] = []

    async def _record() -> None:
        calls.append(1)

    async def _fail_on_second(campaign_id: str, channel: str) -> None:
        if channel == "second":
            msg = "database is locked"
            raise RuntimeError(msg)
        await link_channel_to_campaign(campaign_id, channel)

    monkeypatch.setattr(_runtime, "reconcile_if_running", _record)
    monkeypatch.setattr(db, "link_channel_to_campaign", _fail_on_second)
    campaign_id = await _campaign()

    result = await adopt_candidates(campaign_id, ["first", "second", "third"])

    assert result is not None
    assert [(o.channel, o.status) for o in result.outcomes] == [
        ("first", "linked"),
        ("second", "failed"),
        ("third", "linked"),
    ]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_batch_that_only_fails_links_nothing_and_skips_the_reconcile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The systemic case (campaign gone, DB wedged) degrades into a full failure list."""
    calls: list[int] = []

    async def _record() -> None:
        calls.append(1)

    async def _always_fail(campaign_id: str, channel: str) -> None:  # noqa: ARG001
        msg = "database is locked"
        raise RuntimeError(msg)

    monkeypatch.setattr(_runtime, "reconcile_if_running", _record)
    monkeypatch.setattr(db, "link_channel_to_campaign", _always_fail)
    campaign_id = await _campaign()

    result = await adopt_candidates(campaign_id, ["first", "second"])

    assert result is not None
    assert [o.status for o in result.outcomes] == ["failed", "failed"]
    assert calls == []


@pytest.mark.asyncio
async def test_a_failing_reconcile_does_not_cost_the_batch_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reconcile is a best-effort nudge: the picks are linked whether it works or not.

    Letting it raise would hand the operator the same opaque 500 with no per-channel
    outcomes that the per-channel reporting exists to prevent.
    """

    async def _boom() -> None:
        msg = "listener is wedged"
        raise RuntimeError(msg)

    monkeypatch.setattr(_runtime, "reconcile_if_running", _boom)
    campaign_id = await _campaign()

    result = await adopt_candidates(campaign_id, ["alpha", "beta"])

    assert result is not None
    assert [outcome.status for outcome in result.outcomes] == ["linked", "linked"]
    from core.db import list_campaign_channels  # noqa: PLC0415

    links = await list_campaign_channels(campaign_id)
    assert sorted(link.channel for link in links.links) == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_a_systemic_failure_stops_writing_but_still_reports_every_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """500 doomed writes buy nothing; one outcome per requested channel is the contract."""
    attempted: list[str] = []

    async def _always_fail(campaign_id: str, channel: str) -> None:  # noqa: ARG001
        attempted.append(channel)
        msg = "database is locked"
        raise RuntimeError(msg)

    monkeypatch.setattr(db, "link_channel_to_campaign", _always_fail)
    monkeypatch.setattr(settings.neurocomment, "discovery_max_consecutive_errors", 2)
    campaign_id = await _campaign()

    result = await adopt_candidates(campaign_id, [f"chan_{index}" for index in range(6)])

    assert result is not None
    assert [outcome.status for outcome in result.outcomes] == ["failed"] * 6
    assert attempted == ["chan_0", "chan_1"]


@pytest.mark.asyncio
async def test_an_already_assigned_channel_does_not_count_toward_the_abort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refusal proves the DB is answering, so it must not push the batch over the bound."""
    attempted: list[str] = []

    async def _fail_unless_taken(campaign_id: str, channel: str) -> None:
        attempted.append(channel)
        if channel == "taken":
            await link_channel_to_campaign(campaign_id, channel)
            return
        msg = "database is locked"
        raise RuntimeError(msg)

    theirs = await _campaign("Theirs")
    await link_channel_to_campaign(theirs, "taken")
    monkeypatch.setattr(db, "link_channel_to_campaign", _fail_unless_taken)
    monkeypatch.setattr(settings.neurocomment, "discovery_max_consecutive_errors", 2)
    mine = await _campaign("Mine")

    result = await adopt_candidates(mine, ["aaa", "taken", "bbb", "ccc"])

    assert result is not None
    assert [outcome.status for outcome in result.outcomes] == [
        "failed",
        "already_assigned",
        "failed",
        "failed",
    ]
    # Without the reset the run of failures would span the refusal and skip ``ccc``.
    assert attempted == ["aaa", "taken", "bbb", "ccc"]


@pytest.mark.asyncio
async def test_cancellation_mid_batch_does_not_reconcile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled request must not start a reconcile it would only delay."""
    calls: list[int] = []

    async def _record() -> None:
        calls.append(1)

    async def _cancel_on_second(campaign_id: str, channel: str) -> None:
        if channel == "second":
            raise asyncio.CancelledError
        await link_channel_to_campaign(campaign_id, channel)

    monkeypatch.setattr(_runtime, "reconcile_if_running", _record)
    monkeypatch.setattr(db, "link_channel_to_campaign", _cancel_on_second)
    campaign_id = await _campaign()

    with pytest.raises(asyncio.CancelledError):
        await adopt_candidates(campaign_id, ["first", "second", "third"])

    assert calls == []
