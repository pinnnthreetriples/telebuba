"""An account kicked out of a discussion group gets itself back in — 2 tries, one a day.

The parked pair carries onboarding's hard-join-failure sentinel and nothing used to retry
it: onboarding has no timer, so a kicked account waited for an operator, and a channel
whose every account was kicked produced nothing at all. The rule rides the deletion sweep
(``_rejoin.review_access_lost``), which only POKES onboarding — the joining itself stays
in the onboarding pass, behind its join cap and jitter. Own module because
``test_runtime_sweep`` is already 556 lines against the 700-line test cap.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (  # type: ignore[attr-defined]
    _get_engine,
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
from core.migration_steps_budget_reset import _reset_overshot_retry_budgets
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

if TYPE_CHECKING:
    from schemas.logs import LogEntry

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


async def _rejoin_attempts(account_id: str) -> int:
    row = await fetch_readiness(account_id, _CHANNEL)
    assert row is not None
    return row.rejoin_attempts


def _backdate_rejoin_attempt(account_id: str, *, hours: float) -> None:
    """Age the pair's last stamp, standing in for a re-join window actually elapsing.

    ``stamp_rejoin_attempt`` writes the wall clock, so a review called with a synthetic
    ``now`` still measures against a stamp from a second ago — walking the real timeline
    means moving the row, not only the argument.
    """
    stamp = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE neurocomment_readiness SET rejoin_attempted_at = ? "
            "WHERE account_id = ? AND channel = ?",
            (stamp, account_id, _CHANNEL),
        )


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
    # Waiting out a window is not a verdict: the pair still has an attempt left.
    assert await _channel_is_active(campaign_id) is True

    await _rejoin.review_access_lost(now + timedelta(hours=25))
    assert len(triggered) == 1


@pytest.mark.asyncio
async def test_attempts_stop_at_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole budget spent: the window running out buys no extra attempt.

    A day, not thirty: past two windows the stamp is STALE, and a stale stamp is a row nobody
    was re-joining against rather than a spent budget (see the freshness test below).
    """
    await _campaign("acc-1", "acc-2")  # a second serving account, so nothing is unlinked
    await _park("acc-1", attempts=settings.neurocomment.channel_max_rounds)
    triggered = _pokes(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC) + timedelta(hours=25))

    assert triggered == []


@pytest.mark.asyncio
async def test_the_shipped_budget_is_two_attempts_over_forty_eight_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The operator's rule, walked on the clock: try now, try in a day, drop a day later.

    Unpinned ``channel_max_rounds`` on purpose — the rule IS the shipped default (the same
    number ``_channel_pause`` spends), so lowering or raising it must fail here rather than
    slip past a test that pins its own budget.
    """
    assert settings.neurocomment.channel_max_rounds == 2
    campaign_id = await _campaign("acc-1")
    await _park("acc-1")
    triggered = _pokes(monkeypatch)
    join = _patch_joins(monkeypatch)
    join.set(_CHANNEL, status="failed", error_type="ChannelPrivateError")

    # t≈0: the first sweep tick after the loss spends attempt 1; the pass it pokes fails
    # and re-parks the pair.
    await _rejoin.review_access_lost(datetime.now(UTC))
    await neurocomment.onboard_campaign(campaign_id)
    assert await _rejoin_attempts("acc-1") == 1
    assert await _channel_is_active(campaign_id) is True

    # t=24h: the window is up, so attempt 2 — the last one — goes out.
    _backdate_rejoin_attempt("acc-1", hours=24)
    await _rejoin.review_access_lost(datetime.now(UTC))
    await neurocomment.onboard_campaign(campaign_id)
    assert await _rejoin_attempts("acc-1") == 2
    assert len(triggered) == 2
    assert await _channel_is_active(campaign_id) is True

    # Nothing is due any more, but attempt 2 gets the same day attempt 1 got...
    await _rejoin.review_access_lost(datetime.now(UTC))
    assert await _channel_is_active(campaign_id) is True

    # ...so the channel leaves the campaign at t=48h, not at t=24h.
    _backdate_rejoin_attempt("acc-1", hours=24)
    await _rejoin.review_access_lost(datetime.now(UTC))
    assert await _channel_is_active(campaign_id) is False
    # Two re-joins and never a third — and not one leave: the drop unlinks the channel, and
    # the give-up report no longer knocks on a group Telegram has already ejected us from.
    kinds = [type(action).__name__ for _, action in join.calls]
    assert (kinds.count("JoinDiscussionGroup"), kinds.count("LeaveDiscussionGroup")) == (2, 0)


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
    sweep tick ran another onboarding pass on its behalf. One poke per round, then the
    give-up rule decides — exactly the deal for a pair whose re-joins all fail.
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
    # The last attempt's own window, walked on the row rather than on a synthetic ``now``: a
    # stamp left two windows behind reads as stale and would buy a fresh attempt instead.
    _backdate_rejoin_attempt("acc-1", hours=25)
    await _rejoin.review_access_lost(datetime.now(UTC))
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
    await _park("acc-1", attempts=settings.neurocomment.channel_max_rounds - 1)
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
    # pair still has its whole budget), not approval.
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
    ``JoinDiscussionGroup`` RPC, so the stamp bought exactly zero re-join attempts: every
    pause round burned one against a channel nobody could even try to re-enter — the whole
    budget, at the shipped two — and the give-up log then claimed the accounts had used up
    their re-joins. Deferred, not skipped: the window lapses and the next tick spends one.
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
    # One flat ``channel_pause_hours``, the window the pause rule actually buys.
    await bump_channel_pause(_CHANNEL, (now + timedelta(hours=24)).isoformat())

    await _rejoin.review_access_lost(now + timedelta(hours=23))
    assert await _channel_is_active(campaign_id) is True

    await _rejoin.review_access_lost(now + timedelta(hours=25))
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

    # Past the last attempt's own window — the drop waits for it (see the test below).
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
async def test_the_last_attempt_gets_its_whole_window_before_the_channel_goes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The last attempt is stamped a window short of the deadline; its pass joins after.

    Dropping as soon as nothing is ``retry_due`` any more gave it about five minutes: at
    t=24h+5min every pair is exhausted and none is due, so the channel was unlinked with a
    re-join still in flight — the last attempt never got the day the first one did. The
    budget is ``channel_max_rounds`` attempts over as many DAYS, which is what puts the
    shipped drop at 48h rather than 24h.
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


# --------------------------------------------------------------------------- #
# The operator's view: one line per attempt actually spent.
# --------------------------------------------------------------------------- #


async def _attempt_lines() -> list[LogEntry]:
    """The spent-attempt rows, oldest first — the order the operator reads them in."""
    return [
        entry
        for entry in reversed(await list_recent_logs(limit=50))
        if entry.event == "neurocomment_rejoin_attempt"
    ]


@pytest.mark.asyncio
async def test_every_spent_attempt_is_logged_with_its_place_in_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two days of silence, then a channel gone: the rule used to report only its end.

    Spending an attempt wrote nothing, so between the access loss and the WARNING that
    unlinks the channel the operator had no way to tell a channel on its way out from an
    idle one. ``reason`` is the position in the shipped budget, which the SPA appends to
    the label untranslated — "Возвращаемся в чат · 1/2", then "· 2/2".
    """
    await _campaign("acc-1")
    await _park("acc-1")
    _pokes(monkeypatch)
    now = datetime.now(UTC)

    await _rejoin.review_access_lost(now)

    lines = await _attempt_lines()
    assert len(lines) == 1
    assert lines[0].level == "INFO"
    assert lines[0].account_id == "acc-1"
    assert lines[0].extra["channel"] == _CHANNEL
    assert lines[0].extra["attempts"] == 1
    assert lines[0].extra["reason"] == "1/2"

    # A day on, the last attempt goes out and says so.
    _backdate_rejoin_attempt("acc-1", hours=24)
    await _rejoin.review_access_lost(now + timedelta(hours=25))

    lines = await _attempt_lines()
    assert [(line.extra["attempts"], line.extra["reason"]) for line in lines] == [
        (1, "1/2"),
        (2, "2/2"),
    ]


@pytest.mark.asyncio
async def test_a_pair_whose_window_is_still_running_writes_no_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One line per attempt SPENT, not per pair parked on the channel.

    The review walks every parked pair on a channel and spends an attempt only for the
    ones whose window is up; a line for the others would report a re-join that never went
    out and repeat it every five minutes until their day was over.
    """
    await _campaign("acc-1", "acc-2")
    await _park("acc-1")
    await _park("acc-2", attempts=1)  # mid-window: nothing is due for it yet
    _pokes(monkeypatch)

    await _rejoin.review_access_lost(datetime.now(UTC))

    assert [(line.account_id, line.extra["reason"]) for line in await _attempt_lines()] == [
        ("acc-1", "1/2"),
    ]


# --------------------------------------------------------------------------- #
# Migration #48: rows counted against the OLD budget of 4 start over at 2.
# --------------------------------------------------------------------------- #


def _apply_budget_reset() -> None:
    """Run migration #48's body against the live test engine."""
    with _get_engine().begin() as connection:
        _reset_overshot_retry_budgets(connection)


@pytest.mark.asyncio
async def test_a_row_inherited_from_the_old_budget_gets_the_full_new_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 4 → 2 change must not execute the channels it caught mid-timeline (#48).

    A pair that spent 2 of the OLD budget's 4 attempts is instantly ``exhausted`` under the
    new one, and its last window has run out, so ``_review_channel``'s give-up test is true on
    the very first tick after the deploy: the channel is unlinked without one re-join under
    the new rule and without any of the 48h it promises. On the live database that was 23 rows
    across six channels. The migration gives the budget back — the rule changed, so everyone
    it had already spent starts over.

    A stamp one tick past its window, not one left for days: the freshness check now hands a
    row THAT stale an attempt on the new budget by itself, and this knife-edge — a window that
    only just ran out when the setting changed — is the case only a migration can repair.
    """
    campaign_id = await _campaign("acc-1")
    await _park("acc-1", attempts=2)
    _backdate_rejoin_attempt("acc-1", hours=25)
    inherited = await fetch_readiness("acc-1", _CHANNEL)
    assert inherited is not None
    assert _rejoin.exhausted(inherited) is True  # what the first tick would have judged
    pokes = _pokes(monkeypatch)

    _apply_budget_reset()

    restored = await fetch_readiness("acc-1", _CHANNEL)
    assert restored is not None
    assert (restored.rejoin_attempts, restored.rejoin_attempted_at) == (0, None)
    assert _rejoin.exhausted(restored) is False

    await _rejoin.review_access_lost(datetime.now(UTC))

    # The channel survives and actually gets its first attempt under the new rule, rather
    # than a verdict inherited from a budget that no longer exists.
    assert await _channel_is_active(campaign_id) is True
    assert await _rejoin_attempts("acc-1") == 1
    assert len(pokes) == 1
    assert [line.extra["reason"] for line in await _attempt_lines()] == ["1/2"]
