"""The rule for an account kicked out of a discussion group: get itself back in.

A pair that loses chat access is parked with onboarding's hard-join-failure sentinel
``(joined=False, captcha_passed=True, ready=False)`` — by the post path on
``ChannelPrivateError`` and by ``_classify`` on a hard join failure. Both mean this pair
needs a fresh join, and since #44 each writes the Telegram verdict beside the sentinel, so
the two are no longer the same claim: a kick can come good, a handle nobody owns cannot,
and only the first is worth a budget. Nothing retried it, because onboarding has NO timer
(operator Start, boot, and campaign reconciles only), so a kicked account waited for a
human — and a channel whose every account was kicked produced nothing at all, silently.

The rule, deliberately shaped like the two already here (``_sweep._review_join_requests``
for approval-gated joins, ``_channel_pause`` for write-blocked channels): one re-join per
``channel_pause_hours``, at most ``channel_max_rounds`` of them, and once the last of
those windows has run out too, the channel leaves its campaign — four attempts spread
over four days, the fourth one included. It rides the 5-minute deletion sweep — the only
periodic neurocomment task — and pokes onboarding rather than joining itself: the sweep
must spend no join RPCs, and onboarding already owns the join cap and the jitter. The
attempt is spent HERE, as the poke goes out: a counter only the pass could move never
moved for a pair the pass cannot reach (pinned elsewhere, account at its join cap), so
the nag had no bound at all. One case is exempt, and only one — a channel serving out a
``_channel_pause`` window refuses EVERY join, so the review sits the window out rather
than burning three of the four attempts on a channel nobody could try to re-enter.

Counter and deadline are persisted per pair (migration #43) rather than kept in memory,
for the reason ``_channel_pause`` documents: the live app restarts, and a four-day rule
built on module dicts never reaches day four.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    fetch_active_campaigns_for_channels,
    fetch_channel_paused_until,
    list_access_lost_readiness,
    list_campaign_accounts,
    list_channel_readiness,
    stamp_rejoin_attempt,
)
from core.logging import log_event
from services.neurocomment import _state
from services.neurocomment._pins import serving_accounts

if TYPE_CHECKING:
    from schemas.neurocomment import NeurocommentReadiness

# The verdicts that put a re-join out of reach for good (#44). Both families say the
# ADDRESS we hold is dead, not that this chat refused this account: a username nobody owns
# and a revoked invite key resolve to nothing tomorrow either, and the only thing that
# changes them is an operator linking the channel again under a working one — which clears
# the column anyway. Everything else stays retryable, NULL included. A kick
# (ChannelPrivateError / UserNotParticipantError) is the case this whole rule exists for; a
# full group, an account at its channel ceiling and every error Telethon leaves unmapped
# can all come good on their own. A false "hopeless" costs a live channel, where a wasted
# retry costs one join RPC — so anything not provably dead is retried.
_TERMINAL_REASONS = frozenset(
    {
        "UsernameNotOccupiedError",
        "UsernameInvalidError",
        "InviteHashExpiredError",
        "InviteHashInvalidError",
        "InviteHashEmptyError",
    },
)


def access_lost(readiness: NeurocommentReadiness) -> bool:
    """True for the hard-join-failure sentinel — the pair is out and wants back in.

    No other path writes ``captcha_passed`` on an unjoined row, which is what makes the
    sentinel readable. Field for field the SQL predicate in ``_readiness._ACCESS_LOST``,
    including the two exclusions: onboarding refuses to re-join a skipped (#148) or banned
    (#30) pair, so counting one as parked would leave a pair that is due forever — and the
    review would poke onboarding every five minutes for a join that never happens.
    """
    return (
        not readiness.joined
        and readiness.captcha_passed
        and not readiness.ready
        and not readiness.human_skipped
        and not readiness.banned
    )


def terminal(readiness: NeurocommentReadiness) -> bool:
    """True when the verdict that parked this pair rules a re-join out entirely.

    An unknown verdict (NULL — every row from before #44, and any gateway failure that
    carried no error type) is NOT terminal: it reads exactly as it did before the column
    existed, which is what keeps the upgrade behaviour-preserving.
    """
    return readiness.access_lost_reason in _TERMINAL_REASONS


def exhausted(readiness: NeurocommentReadiness) -> bool:
    """True once this pair has used every re-join it will ever get.

    A terminal verdict is exhausted from the first tick: the budget buys evidence, and
    Telegram has already given it. That is one predicate, not two, because everything that
    reads the budget wants the same answer — the board badges ``join_failed`` instead of
    promising a return that cannot happen, and the sweep spends no attempt on it.

    Public alongside :func:`access_lost` because ``board`` badges the two together — a
    parked pair still inside its budget is ``rejoining``, one past it is ``join_failed``
    — and reading the budget off this rule is what keeps the badge from claiming a pair
    is finished while the sweep is still retrying it.
    """
    return terminal(readiness) or (
        readiness.rejoin_attempts >= settings.neurocomment.channel_max_rounds
    )


def _window_elapsed(readiness: NeurocommentReadiness, now: datetime) -> bool:
    """True once the last stamped attempt has had its full ``channel_pause_hours``.

    False for a never-stamped pair — no attempt has been made, so no window is running.
    """
    if readiness.rejoin_attempted_at is None:
        return False
    attempted = datetime.fromisoformat(readiness.rejoin_attempted_at)
    return now - attempted >= timedelta(hours=settings.neurocomment.channel_pause_hours)


def retry_due(readiness: NeurocommentReadiness, now: datetime) -> bool:
    """True when this pair may spend a re-join right now.

    Never stamped = due immediately: the first retry happens on the sweep tick after the
    kick (~5 minutes), which is what makes a transient access loss — Telethon also raises
    ``ChannelPrivateError`` on a stale cached entity — cost minutes instead of a day. Each
    later attempt waits the full window, and the fourth one is the last.
    """
    if exhausted(readiness):
        return False
    return readiness.rejoin_attempted_at is None or _window_elapsed(readiness, now)


def attempt_owed(readiness: NeurocommentReadiness) -> bool:
    """True while the attempt the review stamped has not been answered by a pass yet.

    The review owns the timer: it stamps, then pokes onboarding. Whatever the poked pass
    then makes of the join, it writes the readiness row (``checked_at``) — so a stamp
    NEWER than the last write is an attempt still owed, and an older one is an attempt
    already made. That is what stops every OTHER trigger of a pass (operator Start, a
    campaign reconcile, another channel's poke) from re-joining every parked pair in the
    fleet for free: Telegram answers ``ok`` for a group the account is already in, so each
    one of those counts against its rolling-24h join cap.

    Never stamped = never answered either, and the first re-join is due immediately (the
    same promise ``retry_due`` makes): a pair parked seconds ago must not have to wait for
    a sweep tick when an operator hits Start.
    """
    if readiness.rejoin_attempted_at is None:
        return True
    return datetime.fromisoformat(readiness.rejoin_attempted_at) > datetime.fromisoformat(
        readiness.checked_at
    )


async def review_access_lost(now: datetime) -> None:
    """Age the parked pairs: poke onboarding for the due ones, drop the dead channels.

    Never raises — a failure here must not abort the deletion sweep that owns this tick.
    """
    try:
        rows = (await list_access_lost_readiness()).readiness
    except Exception as exc:  # noqa: BLE001 - the review must never abort the sweep loop.
        await log_event(
            "WARNING",
            "neurocomment_rejoin_review_failed",
            extra={"error_type": type(exc).__name__},
        )
        return
    by_channel: dict[str, list[NeurocommentReadiness]] = defaultdict(list)
    for row in rows:
        by_channel[row.channel].append(row)
    # Only channels still linked to an ACTIVE campaign can be dropped; the bulk read also
    # hands us the campaign id ``deactivate_channel`` needs.
    campaigns = await fetch_active_campaigns_for_channels(list(by_channel))
    retry_due_somewhere = False
    for channel, channel_rows in by_channel.items():
        campaign = campaigns.get(channel)
        if campaign is None:
            continue
        if await _review_channel(campaign.campaign_id, channel, channel_rows, now):
            retry_due_somewhere = True
    if retry_due_somewhere:
        # Late import: ``_runtime`` reaches this module through the sweep, so a top-level
        # import cycles. Same poke ``_sweep._review_join_requests`` uses — onboarding, not
        # a join RPC from here, does the joining.
        from services.neurocomment import _runtime, _signals  # noqa: PLC0415

        _runtime._ensure_onboarding_running(_signals.signal_onboarding_progress)  # noqa: SLF001


async def _review_channel(
    campaign_id: str,
    channel: str,
    channel_rows: list[NeurocommentReadiness],
    now: datetime,
) -> bool:
    """Age one channel's parked pairs; True when a re-join was authorized this tick.

    Its own function so the caller stays a flat loop over channels — and because the three
    outcomes here (spend, wait, drop) are the whole rule.
    """
    # Only pairs an onboarding pass can actually reach: it walks the campaign's serving
    # accounts, pin-aware. A row left behind by an account since removed from the
    # campaign, or pinned to other channels, is nobody's to retry — reporting it due
    # would poke onboarding every five minutes for a join that never happens.
    serving = serving_accounts((await list_campaign_accounts(campaign_id)).links, channel)
    parked = [row for row in channel_rows if row.account_id in serving and access_lost(row)]
    if not parked:
        return False
    # The one thing the stamp-first design must NOT charge for. "The pass cannot reach this
    # pair" (pinned elsewhere, account at its join cap) still costs an attempt, or a
    # permanently unreachable pair would never terminate — but "no pair on this channel can
    # be tried at all" is a different claim, and a #147 pause is exactly that:
    # ``_onboard_pair`` returns ``channel_paused`` before any join RPC, so three pause
    # rounds (72h) burned three of the four attempts against a channel nobody could even
    # try to re-enter, and the give-up log then said they had used them up. Read off the
    # same column onboarding refuses on, so the two cannot disagree. The drop waits too:
    # while the pause holds, the channel's fate belongs to the pause rule's round counter,
    # and a budget spent against refused joins is no evidence. Deferred, never waived — a
    # pause window is a flat ``channel_pause_hours``, so the timeline picks up where it
    # left off on the first tick after it lapses.
    if _state.channel_paused(await fetch_channel_paused_until(channel), now):
        return False
    due = [row for row in parked if retry_due(row, now)]
    if due:
        # The attempt is spent HERE, not by the pass we are about to poke: a pair the pass
        # never reaches (its account at the join cap, its group gone, ...) would otherwise
        # stay due forever, and every sweep tick would run another full onboarding pass on
        # its behalf. The channel's own pause is the exception, and it never gets this far
        # — the guard above sat the window out.
        for row in due:
            await stamp_rejoin_attempt(row.account_id, channel)
        return True
    # Unlike the join-request review, the retry above is NOT gated on the channel being
    # otherwise dead: a kicked account must get back into a chat the other five comment in
    # fine. Only the DROP is, and that check lives one call down — the single place that
    # decides whether anything still works here.
    # A terminal pair has no window to wait out — it never spent an attempt, so there is
    # no re-join in flight to give a day to. Written as the exception it is, so the
    # four-attempts-over-four-DAYS promise below keeps reading off one condition.
    if not all(exhausted(row) and (terminal(row) or _window_elapsed(row, now)) for row in parked):
        # Nothing due, but somebody is still mid-timeline — either they have attempts left,
        # or they have just spent the last one. That second half is why the window check is
        # here and not folded into ``exhausted``: the fourth attempt is stamped at t=72h
        # and the pass it pokes joins *after* that, so dropping the moment nothing is
        # ``retry_due`` any more gave attempt four about five minutes and unlinked the
        # channel with a re-join still in flight. The budget is four attempts over four
        # DAYS: the last window counts like the other three.
        return False
    await _drop_channel_if_nothing_works(campaign_id, channel, serving, parked)
    return False


async def _drop_channel_if_nothing_works(
    campaign_id: str,
    channel: str,
    serving: list[str],
    parked: list[NeurocommentReadiness],
) -> None:
    """Unlink a channel every serving account has run out of re-joins for.

    The coverage rule of ``bans._unlink_channel_if_no_account_left``, and for its reason:
    a serving account with NO readiness row was never tried here, not tried and failed,
    and onboarding reaches a fleet slowly. So every serving account must have a row, and
    any usable one keeps the channel.
    """
    rows = (await list_channel_readiness(campaign_id, channel, serving)).readiness
    if len(rows) != len(serving) or any(row.ready for row in rows):
        return
    # Via the service, not the repository, so the listener reconciles and stops watching
    # the channel — exactly like the two sibling rules.
    from services.neurocomment import campaigns as campaigns_service  # noqa: PLC0415

    await campaigns_service.deactivate_channel(campaign_id, channel)
    # Two verdicts, two lines: "every account spent its four re-joins here" is a claim
    # about the chat, and reporting it for a channel whose address Telegram says does not
    # exist told the operator to wait for a recovery nobody was working on. Both codes are
    # written out in full — the i18n drift guard reads the literal at the call site, and a
    # single call picking its code from a variable would hide both from it.
    if all(terminal(row) for row in parked):
        await log_event(
            "WARNING",
            "neurocomment_channel_join_impossible",
            extra={
                "channel": channel,
                "campaign_id": campaign_id,
                "parked_accounts": len(parked),
                "reason": "join_impossible",
                # Telegram's own words, deduped: the only thing that tells an operator
                # whether linking the channel again under a corrected handle would help.
                "error_types": sorted({row.access_lost_reason for row in parked if terminal(row)}),
            },
        )
        return
    await log_event(
        "WARNING",
        "neurocomment_channel_rejoin_exhausted",
        extra={
            "channel": channel,
            "campaign_id": campaign_id,
            "parked_accounts": len(parked),
            "attempts": settings.neurocomment.channel_max_rounds,
            "reason": "rejoin_exhausted",
        },
    )
