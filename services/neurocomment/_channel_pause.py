"""The rule for a channel that will not let us write (#147).

K consecutive write failures on a channel end a *round*: it is paused for a flat
``channel_pause_hours``, in which nothing posts there and no account is onboarded to it,
and its round counter goes up. The round that reaches ``channel_max_rounds`` pauses like
every other one; the channel leaves its campaign when THAT window runs out — but only once
every account that serves it has actually been tried there; while any has not, the spent
window is released, the counter keeps climbing and the channel gets another round. A
delivered comment clears both — the channel demonstrably works.

The escalating 1h→2h→…→24h back-off this replaced only delayed the verdict; flat days
actually reach one. As shipped the budget is ``channel_max_rounds=2``: round 1 pauses the
channel for a day, the next K failures after that window end round 2, and the day round 2
buys is the last — the channel goes at t=48h, the same two-attempts-48h-apart rule
``_rejoin`` and ``_sweep._review_join_requests`` run on the same setting. Sitting that last
window out needs a tick from OUTSIDE this rule, because the rule is driven by post attempts
and a paused channel takes none: ``review_expired_pauses`` rides the 5-minute deletion
sweep and delivers the verdict its last round deferred. Dropping the channel the moment
round 2 ended instead cost it a whole window — ~24h against the 48h its two siblings give.

Round counter and deadline are persisted on the campaign link (migration #42) rather than
kept in module dicts: the live app restarted 7 times in three days, so a multi-day rule
built on memory never reached its last round. Only the consecutive-failure *window* stays
in memory (``_state``) — losing it on a restart costs at most one round boundary, not the
verdict.

Its own module because ``_generate`` is at the file-size cap and because this is a
distinct concern from generating a comment: ``_generate`` classifies one post's outcome,
this decides the channel's fate across days.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    bump_channel_pause,
    clear_channel_pause,
    fetch_active_campaigns_for_channels,
    fetch_channel_paused_until,
    list_campaign_accounts,
    list_channel_readiness,
    list_expired_channel_pauses,
    release_channel_pause,
)
from core.logging import log_event
from services.neurocomment import _rejoin, _state
from services.neurocomment._pins import serving_accounts

if TYPE_CHECKING:
    from schemas.neurocomment import NeurocommentReadiness


async def register_write_failure(channel: str, account_id: str) -> None:
    """Count a write failure on ``channel``; end a round when it reaches K.

    The counter is per-CHANNEL, but ``account_id`` is the account whose failure ended the
    round: the neurocomment feed is read one line per account action, and a row with no
    account is the one an operator can't act on.

    No account leaves the chat on the way out, unlike a confirmed personal ban: this is
    the channel forbidding comments, membership costs nothing, and re-joining would spend
    the rolling-24h join cap.
    """
    nc = settings.neurocomment
    if not _state.register_write_failure(
        channel, min_failures=nc.channel_challenge_backoff_min_failures
    ):
        return
    until = datetime.now(UTC) + timedelta(hours=nc.channel_pause_hours)
    pause = await bump_channel_pause(channel, until.isoformat())
    if pause is None:  # the channel lost its active link meanwhile — nothing to pause.
        return
    extra: dict[str, object] = {
        "channel": channel,
        "rounds": pause.pause_rounds,
        "max_rounds": nc.channel_max_rounds,
        "paused_until": pause.paused_until,
        # Which round this is, beside the event label rather than inside ``extra`` where
        # only a developer looks: ``eventReason`` renders an unmapped ``reason`` raw, so
        # the ratio costs no translation (the same trick ``_rejoin`` and the join-request
        # rule already use for their budgets). CLAMPED, because this counter is one of the
        # few that can outrun its own budget: ``review_expired_pauses`` releases a spent
        # window when the fleet is not complete here, and the channel then earns round 3
        # against a budget of 2. "2/2" for each of those says the true thing — the budget
        # is spent and the drop is waiting on something else — where "3/2" would only
        # look like a bug. The raw round stays in ``rounds`` above.
        "reason": f"{min(pause.pause_rounds, nc.channel_max_rounds)}/{nc.channel_max_rounds}",
    }
    if pause.pause_rounds >= nc.channel_max_rounds:
        # Reported here, decided in ``review_expired_pauses``: the budget is spent, so
        # this window is the one whose end carries the verdict — unless an account serving
        # the channel has still never been tried there, which is what this line tells the
        # operator is holding it. The pass re-reads coverage at the deadline rather than
        # trusting this number, because that is where the channel's fate is settled.
        untried, _ = await _channel_coverage(pause.campaign_id, channel)
        if untried:
            extra["untried_accounts"] = untried
    await log_event(
        "WARNING",
        "neurocomment_channel_paused",
        account_id=account_id,
        extra=extra,
    )


async def review_expired_pauses(now: datetime) -> None:
    """Deliver the verdict the last round deferred to the end of its window.

    Rides the 5-minute deletion sweep, exactly like its two sibling rules, and for the
    reason that put them there: nothing else ticks. A paused channel takes no posts, so
    ``register_write_failure`` — the only other entry point here — cannot fire while the
    window it must judge is running. Never raises anything the sweep loop's own guard does
    not already catch and name (``neurocomment_sweep_failed``), and touches only channels
    whose deadline has passed: one bulk read per tick, a second only when one of them has
    spent its budget, then per candidate a point re-read and — only if that still holds —
    the two coverage reads.

    A round below the budget is simply not this pass's business — its window elapsing means
    the channel may post again, which is what the engine's own deadline check already does.

    Four things must ALL be true before a channel is unlinked, and each of the last three
    is a defect this pass shipped with:

    1. the campaign is still active — a stopped one posts nothing, so nothing it leaves
       behind is evidence;
    2. the pause is still exactly the one we read a moment ago (a delivery in flight may
       have cleared it mid-tick);
    3. the window is FRESH — a long-expired deadline is a lying row, not a verdict;
    4. the fleet is complete here and no pair is mid-re-join.

    Anything short of a drop that leaves a spent deadline behind releases it, so the
    channel is judged on its NEXT window rather than re-judged on this one every five
    minutes.
    """
    nc = settings.neurocomment
    spent = [
        link
        for link in (await list_expired_channel_pauses(now.isoformat())).links
        if link.pause_rounds >= nc.channel_max_rounds
    ]
    if not spent:
        return
    # Only a channel still linked to an ACTIVE campaign can be dropped — the rule both
    # sibling passes open with. A stopped or paused campaign posts nothing, so the rounds
    # on its links were earned before it stopped and no window running out under it is
    # evidence about the channel; the operator resuming the campaign is what starts the
    # fleet trying again.
    active = await fetch_active_campaigns_for_channels([link.channel for link in spent])
    for link in spent:
        if link.channel not in active:
            continue
        if await fetch_channel_paused_until(link.channel) != link.paused_until:
            # Re-read immediately before the verdict, because the bulk read above is a
            # SNAPSHOT and the thing it snapshots can change under a running delivery. The
            # engine checks the pause ONCE, when a post arrives; generation then takes up
            # to ~245s and the send follows, so a comment can land minutes later — inside
            # this 5-minute tick. That delivery calls ``clear_write_failures``, which zeroes
            # rounds and deadline together, and ``clear_channel_pause`` requires an ACTIVE
            # link, so it cannot undo a drop that already happened: the repair has to be
            # here, before it. Comparing the deadline STRING covers every way the state can
            # have moved on (cleared by a delivery, released by a sibling pass, re-armed by
            # a fresh round) — if it is not byte-for-byte the window we judged, we are not
            # judging it. Reproduced: comment delivered and ``posted``, channel unlinked by
            # the same tick.
            continue
        if _window_stale(link.paused_until, now):
            # A verdict is only ever read off a FRESH window. A deadline that ran out more
            # than a full window ago proves nothing about the channel: nobody was posting
            # while it sat there. That is what a stopped campaign leaves behind (the rounds
            # were earned before the stop, and the operator resuming is supposed to give the
            # fleet a new attempt — the sibling guard above says so, and without this the
            # code did the opposite and dropped the channel on the first tick after the
            # resume), and equally what an app left down for a day leaves behind. Freeing
            # the deadline hands the channel a whole new round to earn its fate in, and
            # incidentally makes any FUTURE shrink of ``channel_max_rounds`` survivable the
            # way migration #48 had to repair the last one by hand.
            await release_channel_pause(link.channel)
            continue
        untried, rows = await _channel_coverage(link.campaign_id, link.channel)
        if untried or _rejoining(rows):
            # The budget is spent but the verdict is not earned, so the counter keeps
            # climbing and the channel gets another round: the first window that ends with
            # the fleet complete drops it. Releasing the spent deadline is what makes that
            # ANOTHER round rather than this same one re-judged every five minutes — the
            # untried accounts need the channel un-paused to be onboarded at all (the pause
            # turns ``_onboard_pair`` away), and they must get their gated post before the
            # next verdict, exactly as every account tried before them did.
            #
            # A pair still inside its re-join budget holds the drop for the same reason and
            # gets the same release. ``_rejoin`` already sits out a pause window rather than
            # burn its budget on a channel nobody can even enter; until now nothing paid
            # that back, so a re-join this very tick logged as "1/2" could be annulled
            # milliseconds later by a pause deadline. The coverage check cannot catch it —
            # a parked pair HAS a readiness row, so it counts as tried. The concession is
            # symmetric now: neither rule executes a channel the other is still working on.
            await release_channel_pause(link.channel)
            continue
        # Late import: ``campaigns`` reaches back here through _runtime -> engine.
        from services.neurocomment import campaigns as campaigns_service  # noqa: PLC0415

        # Via the service, not the repository, so the listener reconciles and stops
        # watching the channel (mirrors ``_sweep._drop_unapproved_channel``).
        await campaigns_service.deactivate_channel(link.campaign_id, link.channel)
        # No ``account_id``: the round that ended here belongs to the channel, not to any
        # one account, and the account whose failure closed it is a tick or a day in the
        # past. The two sibling drops log the same way.
        await log_event(
            "WARNING",
            "neurocomment_channel_dropped",
            extra={
                "channel": link.channel,
                "campaign_id": link.campaign_id,
                "rounds": link.pause_rounds,
                "reason": "write_blocked",
            },
        )


def _window_stale(paused_until: str | None, now: datetime) -> bool:
    """True when this deadline ran out more than a whole window ago.

    The line between "the window this channel was serving just ended" — evidence, and the
    only thing a verdict may be read off — and "this row has simply been lying here", which
    is what a stopped campaign, a long shutdown, or a shrunk ``channel_max_rounds`` leaves
    behind. One full ``channel_pause_hours`` of slack, so an ordinary tick (the sweep runs
    every 5 minutes, and the deadline it judges is at most that old) is never mistaken for
    one.
    """
    if paused_until is None:  # unreachable via ``list_expired_channel_pauses``; NULL never
        return False  # compares <= now. Kept so the predicate is total on its own.
    return now - datetime.fromisoformat(paused_until) > timedelta(
        hours=settings.neurocomment.channel_pause_hours,
    )


def _rejoining(rows: list[NeurocommentReadiness]) -> bool:
    """True while any pair on this channel still has re-joins left to spend.

    Read off ``_rejoin``'s own predicates rather than re-deriving the budget here, so the
    two rules cannot come to different answers about the same row — the same reason
    ``_rejoin`` reads this rule's pause off the column ``_onboard_pair`` refuses on.
    """
    return any(_rejoin.access_lost(row) and not _rejoin.exhausted(row) for row in rows)


async def _channel_coverage(
    campaign_id: str,
    channel: str,
) -> tuple[int, list[NeurocommentReadiness]]:
    """``(accounts serving ``channel`` never tried on it, the rows of those that were)``.

    The count is the coverage rule of ``bans._unlink_channel_if_no_account_left`` and its
    two siblings (``_sweep._drop_unapproved_channel``,
    ``_rejoin._drop_channel_if_nothing_works``), resolved through the one shared pin
    definition so the four cannot drift apart: a serving account with NO readiness row was
    never tried here, not tried and failed. Onboarding reaches a fleet slowly — jitter plus
    the rolling-24h join cap — and this rule's own pause turns it away meanwhile, so without
    the check three gated accounts unlinked a channel the campaign's other three had never
    once opened.

    Their SECOND clause — any still-usable row keeps the channel — is deliberately absent:
    no row here can carry that meaning. ``ready`` says selectable, which every account is
    right up to the moment the gate hits it, and the only proof this channel takes comments
    is a delivered one, which zeroes the rounds through ``clear_write_failures`` anyway.

    The rows come back with the count because the verdict needs both and they have to
    describe the same instant: how much of the fleet is still missing, and what the pairs
    that ARE here are currently doing. Two reads, and only for a channel that has spent its
    round budget: once as the round that spends it ends (the report), and once more at the
    deadline that judges it — where the answer, not the report, is what decides.
    """
    links = (await list_campaign_accounts(campaign_id)).links
    serving = serving_accounts(links, channel)
    rows = (await list_channel_readiness(campaign_id, channel, serving)).readiness
    return len(serving) - len(rows), rows


async def clear_write_failures(channel: str) -> None:
    """A comment was delivered: drop ``channel``'s failure window AND its rounds.

    Both, not just the window: sporadic failures across many successes must not
    accumulate to K, and a channel that works again must not carry an old round into
    its next bad day. Keyed on a *solved challenge* the reset never fired on a channel
    that issues none, and since gates feed the same counter, isolated per-account gates
    would accumulate with no decay and eventually pause a channel the other accounts post
    to fine.
    """
    _state.reset_write_failures(channel)
    await clear_channel_pause(channel)
