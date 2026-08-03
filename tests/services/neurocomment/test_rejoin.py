"""An account kicked out of a discussion group gets itself back in — 4 tries, one a day.

The parked pair carries onboarding's hard-join-failure sentinel and nothing used to retry
it: onboarding has no timer, so a kicked account waited for an operator, and a channel
whose every account was kicked produced nothing at all. The rule rides the deletion sweep
(``_rejoin.review_access_lost``), which only POKES onboarding — the joining itself stays
in the onboarding pass, behind its join cap and jitter. Own module because
``test_runtime_sweep`` is already 556 lines against the 700-line test cap.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.config import settings
from core.db import (
    assign_account_to_campaign,
    bump_channel_pause,
    create_account,
    create_campaign,
    deactivate_channel,
    fetch_readiness,
    link_channel_to_campaign,
    list_campaign_channels,
    list_recent_logs,
    mark_human_skipped,
    record_join,
    stamp_join_request,
    stamp_rejoin_attempt,
    upsert_readiness,
)
from core.repositories.neurocomment import set_campaign_account_channels
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate, CampaignStatus
from services import neurocomment
from services.neurocomment import _rejoin, _runtime, _seams, onboarding
from services.neurocomment.board import load_neurocomment_board
from tests.services.neurocomment.onboarding_support import (
    _JoinStub,
    _no_sleep,
    _ReadStub,
)

pytestmark = pytest.mark.usefixtures("isolate_onboarding")

_CHANNEL = "@chan"


async def _campaign(*accounts: str, status: CampaignStatus = "active") -> str:
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status=status))
    await link_channel_to_campaign(campaign.campaign_id, _CHANNEL)
    for account_id in accounts:
        await create_account(AccountCreate(account_id=account_id, session_name=account_id))
        await assign_account_to_campaign(campaign.campaign_id, account_id)
    return campaign.campaign_id


async def _park(account_id: str, *, attempts: int = 0) -> None:
    """Leave the pair exactly as a post-time access loss does, with ``attempts`` spent.

    A *spent* attempt is one the onboarding pass already answered, so each stamp is
    followed by the re-park a failed re-join writes — that later readiness write is what
    tells onboarding the attempt is no longer owed to it.
    """
    await upsert_readiness(account_id, _CHANNEL, joined=False, captcha_passed=True, ready=False)
    for _ in range(attempts):
        await stamp_rejoin_attempt(account_id, _CHANNEL)
        await upsert_readiness(account_id, _CHANNEL, joined=False, captcha_passed=True, ready=False)


async def _channel_is_active(campaign_id: str) -> bool:
    links = (await list_campaign_channels(campaign_id)).links
    return any(link.channel == _CHANNEL and link.active for link in links)


def _pokes(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Capture the onboarding pokes instead of spawning a real pass."""
    triggered: list[object] = []
    monkeypatch.setattr(_runtime, "_ensure_onboarding_running", triggered.append)
    return triggered


def _patch_joins(monkeypatch: pytest.MonkeyPatch) -> _JoinStub:
    read = _ReadStub(linked_chat_id=4423, comments_enabled=True)
    join = _JoinStub()
    monkeypatch.setattr(_seams, "execute_read", read.execute_read)
    monkeypatch.setattr(_seams, "execute", join.execute)
    monkeypatch.setattr(onboarding.asyncio, "sleep", _no_sleep([]))
    return join


# --------------------------------------------------------------------------- #
# The retry timeline: the sweep pokes onboarding, once per window, four times.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_freshly_kicked_pair_is_retried_by_the_very_next_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A never-retried pair is due immediately — a transient kick costs minutes, not a day."""
    await _campaign("acc-1")
    await _park("acc-1")
    triggered = _pokes(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC))

    assert len(triggered) == 1


@pytest.mark.asyncio
async def test_a_spent_attempt_is_not_retried_before_the_window_and_is_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    campaign_id = await _campaign("acc-1")
    await _park("acc-1", attempts=1)
    triggered = _pokes(monkeypatch)

    await _rejoin.review_access_lost(now + timedelta(hours=23))
    assert triggered == []
    # Waiting out a window is not a verdict: the pair still has three attempts left.
    assert await _channel_is_active(campaign_id) is True

    await _rejoin.review_access_lost(now + timedelta(hours=25))
    assert len(triggered) == 1


@pytest.mark.asyncio
async def test_attempts_stop_at_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Four spent attempts: no fifth, however long the pair sits there."""
    await _campaign("acc-1", "acc-2")  # a second serving account, so nothing is unlinked
    await _park("acc-1", attempts=settings.neurocomment.channel_max_rounds)
    triggered = _pokes(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC) + timedelta(days=30))

    assert triggered == []


@pytest.mark.asyncio
async def test_a_skipped_pair_that_looks_parked_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator skip on an unjoined pair matches the sentinel field for field.

    Onboarding refuses to re-join it, so counting it as parked would make it due forever
    and poke onboarding on every single sweep tick.
    """
    campaign_id = await _campaign("acc-1")
    await _park("acc-1")
    await mark_human_skipped("acc-1", _CHANNEL)
    triggered = _pokes(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC) + timedelta(days=30))

    assert triggered == []
    assert await _channel_is_active(campaign_id) is True


@pytest.mark.asyncio
async def test_a_parked_pair_the_pass_cannot_reach_is_not_this_rules_business(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An account pinned away from this channel is never onboarded against it.

    Nothing can spend such a pair's attempts, so a review scoped by channel alone reported
    it due forever: a full onboarding pass every five minutes, each firing a real join for
    every not-yet-working pair in the fleet.
    """
    campaign_id = await _campaign("acc-1", "acc-2")
    await link_channel_to_campaign(campaign_id, "@other")
    await set_campaign_account_channels(campaign_id, "acc-1", ["@other"])
    await _park("acc-1")
    triggered = _pokes(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC) + timedelta(days=30))

    assert triggered == []
    row = await fetch_readiness("acc-1", _CHANNEL)
    assert row is not None
    assert row.rejoin_attempts == 0
    assert await _channel_is_active(campaign_id) is True


@pytest.mark.asyncio
async def test_a_poke_costs_an_attempt_even_when_the_pass_never_joins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The counter belongs to the review, not to the join.

    An account at its rolling-24h join cap has the pair skipped before any join RPC, so a
    counter only the pass could move never moved: the pair stayed due forever and every
    sweep tick ran another onboarding pass on its behalf. Four pokes, then the give-up
    rule decides — exactly the deal for a pair whose re-joins all fail.
    """
    monkeypatch.setattr(settings.neurocomment, "max_joins_per_account_per_day", 1)
    campaign_id = await _campaign("acc-1")
    await _park("acc-1")
    await record_join("acc-1")  # the account is now at its cap
    triggered = _pokes(monkeypatch)
    join = _patch_joins(monkeypatch)
    now = datetime.now(UTC)

    for spent in range(1, settings.neurocomment.channel_max_rounds + 1):
        await _rejoin.review_access_lost(now + timedelta(hours=25 * spent))
        await neurocomment.onboard_campaign(campaign_id)
        row = await fetch_readiness("acc-1", _CHANNEL)
        assert row is not None
        assert row.rejoin_attempts == spent

    assert join.calls == []  # the account never left its cap, so nothing was ever joined
    await _rejoin.review_access_lost(now + timedelta(days=30))
    assert len(triggered) == settings.neurocomment.channel_max_rounds
    assert await _channel_is_active(campaign_id) is False


@pytest.mark.asyncio
async def test_re_linking_the_channel_clears_the_pair_counters() -> None:
    """Linking a channel is a fresh start, counters included.

    The give-up log tells the operator to link the channel again. The pause rounds reset
    with the new link row, but the per-pair counters live on readiness and survived: at
    max attempts onboarding refused to join, so nothing happened and the give-up rule
    dropped the channel again on the next sweep tick.
    """
    campaign_id = await _campaign("acc-1")
    await _park("acc-1", attempts=settings.neurocomment.channel_max_rounds)
    await stamp_join_request("acc-1", _CHANNEL)
    await deactivate_channel(campaign_id, _CHANNEL)

    await link_channel_to_campaign(campaign_id, _CHANNEL)

    row = await fetch_readiness("acc-1", _CHANNEL)
    assert row is not None
    assert (row.rejoin_attempts, row.rejoin_attempted_at) == (0, None)
    assert (row.join_request_attempts, row.join_requested_at) == (0, None)


# --------------------------------------------------------------------------- #
# The onboarding half: the pass is what joins, and only when the review asked.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_onboarding_does_not_re_join_a_pair_the_review_did_not_ask_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator Start must not fire a join the daily rule never authorized.

    Every trigger of a pass — Start, a campaign reconcile, another channel's poke — walks
    every parked pair, and Telegram answers ``ok`` for a group we are already in, so each
    one counts against the account's 20/day cap.
    """
    campaign_id = await _campaign("acc-1")
    await _park("acc-1", attempts=1)
    join = _patch_joins(monkeypatch)

    result = await neurocomment.onboard_campaign(campaign_id)

    assert join.calls == []
    assert [(o.state, o.reason) for o in result.outcomes] == [("joining", "rejoin_backoff")]


@pytest.mark.asyncio
async def test_a_successful_re_join_clears_the_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Back in the group: the next access loss starts from attempt one, not the cap."""
    campaign_id = await _campaign("acc-1")
    await _park("acc-1", attempts=3)
    _pokes(monkeypatch)
    join = _patch_joins(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC) + timedelta(hours=25))
    result = await neurocomment.onboard_campaign(campaign_id)

    assert [account_id for account_id, _ in join.calls] == ["acc-1"]
    assert [o.state for o in result.outcomes] == ["ready"]
    row = await fetch_readiness("acc-1", _CHANNEL)
    assert row is not None
    assert (row.rejoin_attempts, row.rejoin_attempted_at) == (0, None)


@pytest.mark.asyncio
async def test_a_failed_re_join_spends_exactly_one_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One authorization, one RPC: the re-parked sentinel cannot retry forever."""
    campaign_id = await _campaign("acc-1")
    await _park("acc-1")
    _pokes(monkeypatch)
    join = _patch_joins(monkeypatch)
    join.set(_CHANNEL, status="failed", error_type="ChannelPrivateError")

    await _rejoin.review_access_lost(datetime.now(UTC))
    await neurocomment.onboard_campaign(campaign_id)

    row = await fetch_readiness("acc-1", _CHANNEL)
    assert row is not None
    # Still the sentinel (so the rule keeps applying), and one attempt lighter.
    assert (row.joined, row.captcha_passed, row.ready) == (False, True, False)
    assert row.rejoin_attempts == 1
    # ...and a second pass right away spends nothing more.
    await neurocomment.onboard_campaign(campaign_id)
    assert len(join.calls) == 1


@pytest.mark.asyncio
async def test_a_paused_channel_leaves_a_parked_pair_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pause is a verdict on the channel, not on the pair, so readiness is left as is.

    Rewriting the row here erased the access-lost sentinel: the pair fell out of this rule
    for good, and once the deadline passed the board called it ``join_by_request`` —
    "awaiting admin approval" for a channel where no request was ever sent.
    """
    campaign_id = await _campaign("acc-1")
    await _park("acc-1")
    join = _patch_joins(monkeypatch)
    await bump_channel_pause(_CHANNEL, (datetime.now(UTC) + timedelta(hours=24)).isoformat())

    result = await neurocomment.onboard_campaign(campaign_id)

    assert join.calls == []
    assert [o.state for o in result.outcomes] == ["channel_paused"]
    row = await fetch_readiness("acc-1", _CHANNEL)
    assert row is not None
    assert (row.joined, row.captcha_passed, row.ready) == (False, True, False)
    # ...and with the deadline behind it the board reports the re-join in progress (the
    # pair still has all four attempts), not approval.
    await bump_channel_pause(_CHANNEL, (datetime.now(UTC) - timedelta(seconds=1)).isoformat())
    board = await load_neurocomment_board(campaign_id)
    assert board is not None
    assert board.channels[0].status == "rejoining"


@pytest.mark.asyncio
async def test_a_paused_channel_spends_no_re_join_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pause refuses every join, so a poke against it must cost nothing.

    ``_onboard_pair`` turns the pass away with ``channel_paused`` before any
    ``JoinDiscussionGroup`` RPC, so the stamp bought exactly zero re-join attempts: three
    pause rounds (72h) burned three of the four against a channel nobody could even try to
    re-enter, and the give-up log then claimed the accounts had used up their re-joins.
    The budget is deferred, not skipped — the window lapses and the next tick spends one.
    """
    campaign_id = await _campaign("acc-1")
    await _park("acc-1")
    triggered = _pokes(monkeypatch)
    now = datetime.now(UTC)
    await bump_channel_pause(_CHANNEL, (now + timedelta(hours=24)).isoformat())

    await _rejoin.review_access_lost(now)

    assert triggered == []
    row = await fetch_readiness("acc-1", _CHANNEL)
    assert row is not None
    assert row.rejoin_attempts == 0
    assert await _channel_is_active(campaign_id) is True

    await _rejoin.review_access_lost(now + timedelta(hours=25))

    assert len(triggered) == 1
    row = await fetch_readiness("acc-1", _CHANNEL)
    assert row is not None
    assert row.rejoin_attempts == 1


@pytest.mark.asyncio
async def test_a_paused_channel_is_not_dropped_by_the_re_join_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """While the pause holds, the channel's fate belongs to the pause rule's round counter.

    Its own verdict is the one earned here — a re-join budget spent against a channel that
    refused every join is no evidence at all. Deferred, not waived: the pause window is a
    flat ``channel_pause_hours``, so the give-up lands as soon as it lapses.
    """
    campaign_id = await _campaign("acc-1")
    now = datetime.now(UTC)
    await _park("acc-1", attempts=settings.neurocomment.channel_max_rounds)
    _pokes(monkeypatch)
    await bump_channel_pause(_CHANNEL, (now + timedelta(hours=48)).isoformat())

    await _rejoin.review_access_lost(now + timedelta(hours=25))
    assert await _channel_is_active(campaign_id) is True

    await _rejoin.review_access_lost(now + timedelta(hours=49))
    assert await _channel_is_active(campaign_id) is False


# --------------------------------------------------------------------------- #
# Give-up: the channel leaves its campaign only when nothing works there.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_last_exhausted_account_unlinks_the_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id = await _campaign("acc-1", "acc-2")
    for account_id in ("acc-1", "acc-2"):
        await _park(account_id, attempts=settings.neurocomment.channel_max_rounds)
    _pokes(monkeypatch)

    # Past the fourth attempt's own window — the drop waits for it (see the test below).
    await _rejoin.review_access_lost(datetime.now(UTC) + timedelta(hours=25))

    assert await _channel_is_active(campaign_id) is False
    dropped = next(
        entry
        for entry in await list_recent_logs(limit=50)
        if entry.event == "neurocomment_channel_rejoin_exhausted"
    )
    assert dropped.level == "WARNING"
    assert dropped.extra["channel"] == _CHANNEL
    assert dropped.extra["parked_accounts"] == 2
    assert dropped.extra["reason"] == "rejoin_exhausted"


@pytest.mark.asyncio
async def test_the_fourth_attempt_gets_its_whole_window_before_the_channel_goes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Attempt four is stamped at t=72h and the pass it pokes joins *after* that.

    Dropping as soon as nothing is ``retry_due`` any more gave it about five minutes: at
    t=72h+5min every pair is exhausted and none is due, so the channel was unlinked with a
    re-join still in flight — the fourth attempt never got the day the other three did.
    The budget is four attempts over four DAYS.
    """
    campaign_id = await _campaign("acc-1")
    now = datetime.now(UTC)
    await _park("acc-1", attempts=settings.neurocomment.channel_max_rounds)
    _pokes(monkeypatch)

    await _rejoin.review_access_lost(now + timedelta(minutes=5))
    assert await _channel_is_active(campaign_id) is True  # the last re-join is still live

    await _rejoin.review_access_lost(now + timedelta(hours=25))
    assert await _channel_is_active(campaign_id) is False


@pytest.mark.asyncio
async def test_a_channel_with_a_ready_account_is_never_unlinked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One stubborn account must not kill a channel the others comment in fine."""
    campaign_id = await _campaign("acc-1", "acc-2")
    await _park("acc-1", attempts=settings.neurocomment.channel_max_rounds)
    await upsert_readiness("acc-2", _CHANNEL, joined=True, captcha_passed=True, ready=True)
    _pokes(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC))

    assert await _channel_is_active(campaign_id) is True


@pytest.mark.asyncio
async def test_a_serving_account_with_no_readiness_row_keeps_the_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing row means that account was never tried here — not that it failed."""
    campaign_id = await _campaign("acc-1", "acc-2")
    await _park("acc-1", attempts=settings.neurocomment.channel_max_rounds)
    _pokes(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC))

    assert await _channel_is_active(campaign_id) is True


@pytest.mark.asyncio
async def test_a_channel_outside_an_active_campaign_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A paused campaign posts nothing, so its channels are neither retried nor dropped."""
    campaign_id = await _campaign("acc-1", status="paused")
    await _park("acc-1")
    triggered = _pokes(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC))

    assert triggered == []
    assert await _channel_is_active(campaign_id) is True
