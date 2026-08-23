"""Single-pair onboarding helpers extracted from ``onboarding``.

Split out so :mod:`services.neurocomment.onboarding` stays under the aislop
file-size cap. These helpers resolve one channel's linked discussion group,
join it for one account, classify the join result, and run the challenge
solver — persisting the pair's readiness. The public entrypoint
``onboard_account_channel`` and the campaign loop's ``_join_pair_safely`` /
``_resolve_group_for_join`` both build on this chain; ``onboarding`` re-exports
the names it (and the tests) reach for.

All Telegram and randomness access goes through ``_seams`` so tests patch one
place.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.config import settings
from core.db import (
    count_account_joins_since,
    fetch_active_campaign_for_channel,
    fetch_channel_paused_until,
    fetch_readiness,
    record_join,
    upsert_linked_group,
)
from core.logging import log_event
from schemas.neurocomment import AccountChannelOnboarding, NeurocommentReadiness
from schemas.telegram_actions import (
    GetLinkedDiscussionGroup,
    JoinDiscussionGroup,
    LinkedDiscussionGroupResult,
)
from services._account_limits import account_join_cap
from services._join_lock import join_lock
from services.neurocomment import _comments_off, _rejoin, _seams, _state

# The join ActionResult → OnboardingState mapping + solver recording live in
# ``_classify`` (file-size cap); ``_join_and_classify`` below delegates to it.
from services.neurocomment._classify import _classify_join
from services.neurocomment._onboarding_owner import ensure_current


def _effective_solver_enabled(campaign_override: bool | None) -> bool:  # noqa: FBT001 - tri-state value
    """Per-campaign solver override beats the global flag; both default off (#148)."""
    if campaign_override is not None:
        return campaign_override
    return settings.neurocomment.challenge_solver_enabled


async def onboard_account_channel(account_id: str, channel: str) -> AccountChannelOnboarding:
    """Prepare one account to comment on one channel; persist its readiness."""
    linked = await _safe_resolve(account_id, channel)
    if linked is None:
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="failed",
            reason="resolve_failed",
        )
    if not linked.comments_enabled or linked.linked_chat_id is None:
        # comments_off is a channel property, not a per-account state, so we
        # record no readiness row — the campaign loop also short-circuits it.
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="comments_off",
        )
    # Rolling-24h join cap (anti-freeze): the single-pair path funnels through here, so
    # the gate lives here to cover it — the campaign loop gates in _onboard_pair before
    # its jitter sleep. At cap: skip the join RPC (no record), retry once the window rolls.
    # Non-terminal "joining" so the pair is reconsidered, not stuck.
    if await _at_join_cap(account_id):
        return await _daily_cap_outcome(account_id, channel)
    campaign = await fetch_active_campaign_for_channel(channel)
    solver_enabled = _effective_solver_enabled(campaign.solver_enabled if campaign else None)
    return await _join_and_classify(
        account_id, channel, linked.linked_chat_id, solver_enabled=solver_enabled
    )


async def _join_and_classify(
    account_id: str,
    channel: str,
    group_id: int,
    *,
    solver_enabled: bool,
) -> AccountChannelOnboarding:
    """Join the (already-resolved, comment-enabled) group and persist readiness.

    A channel serving out a pause (#147, K consecutive write failures) is left alone — no
    join, no solver — until its deadline passes; the board renders it ``channel_paused``
    off the same persisted column. One point read per pair, which this loop (jittered
    sleeps between joins) can afford where the engine's per-post path could not.

    An operator-skipped pair (#148), an auto-banned pair (#30) or one that gave up on the
    chat's captcha (#49) is left alone: re-joining would run the solver and flip readiness
    back to ready, undoing the skip / reviving the ban / re-entering a group the captcha
    rule just walked out of. The third is what makes that rule's verdict terminal at all —
    without this guard the next pass re-joins and re-solves, which is the exact loop it
    exists to end — so it reports ``bot_challenge``, the wall that is really still there.
    All three are one-way since #49: the «Повторить» button and the ``retry_pair`` behind it
    were the only thing that ever deleted a readiness row, and they are gone. A ban can
    still be lifted by a can_send probe; the skip and the give-up cannot, by design.

    A pair with an approval request still in flight is left alone the same way, and sits
    next to those guards for the same reason: both must cost zero join RPCs. An admin
    reads a re-request as spam, and every pass used to send one.

    Past the guards, the join runs under ``services._join_lock``, the mutex neuroshilling's
    ``join_target`` takes over the same rolling-24h log. Nothing on this path reads the
    ownership registry — the ``busy_neuroshilling`` gate belongs to the engine's per-post
    account selection — so an account a campaign is already driving arrives here like any
    other, and the shared mutex is the whole of what keeps their joins apart.
    """
    existing = await fetch_readiness(account_id, channel)
    if existing is not None and (
        existing.human_skipped or existing.banned or existing.captcha_gave_up
    ):
        # ``bot_challenge`` for the give-up, not a state of its own: the wall really is
        # still the guardian bot, which is also what the board reads off the untouched
        # ``joined and not captcha_passed`` triple — so the two cannot disagree.
        state = (
            "human_skipped"
            if existing.human_skipped
            else "banned"
            if existing.banned
            else "bot_challenge"
        )
        return AccountChannelOnboarding(account_id=account_id, channel=channel, state=state)
    if existing is not None and _join_request_in_flight(existing, datetime.now(UTC)):
        # One line per pair per pass, and only for pairs actually held back — the old
        # behaviour logged a fresh join_by_request every pass *and* paid the RPC for it.
        await log_event(
            "INFO",
            "neurocomment_onboard_join_request_pending",
            account_id=account_id,
            extra={
                "channel": channel,
                "attempts": existing.join_request_attempts,
                # "2/2" next to the event label: ``eventReason`` joins ``extra.reason``
                # onto the caption with ' · ' and prints an unmapped code verbatim, so a
                # bare ratio needs no translation. ``attempts`` alone told the operator
                # nothing — the budget it is spending lives in settings, not on screen.
                "reason": (
                    f"{existing.join_request_attempts}"
                    f"/{settings.neurocomment.join_request_max_attempts}"
                ),
            },
        )
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="join_by_request",
        )
    if _state.channel_paused(await fetch_channel_paused_until(channel), datetime.now(UTC)):
        # Readiness is deliberately NOT written: a pause is a verdict on the channel, not
        # on this pair, and the row we would overwrite may be the access-lost sentinel —
        # erasing it drops the pair out of the re-join rule for good and makes the board
        # render "awaiting admin approval" for a request nobody ever sent.
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="channel_paused",
        )
    if (
        existing is not None
        and _rejoin.access_lost(existing)
        and not _rejoin.attempt_owed(existing)
    ):
        # A pair that lost chat access gets one re-join a day, two in total (#43), and
        # the sweep review is what stamps them. Held back here because every operator
        # Start, every campaign reconcile and every other channel's poke starts a pass
        # too: without this guard each of them would re-join every parked pair in the
        # fleet and pin accounts at their 20/day join cap. Nothing gets past it any more:
        # since #49 no path deletes a readiness row, so only the sweep's own stamp does.
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="joining",
            reason="rejoin_backoff",
        )
    async with join_lock(account_id):
        # Re-read the cap here and not only in the two callers: the count they read is
        # spent by whoever charges first, and a neuroshilling campaign holding the same
        # account charges the very same log. Between their read and this join there are
        # several awaits, so without this one a join that had gone over the budget while
        # it waited still went out. Same outcome the callers produce for a full budget —
        # a non-terminal "joining", reconsidered once the 24h window rolls.
        if await _at_join_cap(account_id):
            return await _daily_cap_outcome(account_id, channel)
        ensure_current()
        result = await _seams.execute(account_id, JoinDiscussionGroup(channel=channel))
        ensure_current()
        if result.status == "ok":
            # A real join RPC landed → count it against the account's rolling-24h cap.
            # ``already_participant`` is a no-op re-join (still a success below) and must
            # NOT be recorded, else a re-onboard would inflate the cap with joins that
            # never happened.
            await record_join(account_id)
    outcome = await _classify_join(
        account_id, channel, result, group_id, solver_enabled=solver_enabled
    )
    await _log_join_wins(
        account_id,
        channel,
        existing,
        outcome,
        member=result.status in {"ok", "already_participant"},
    )
    return outcome


async def _log_join_wins(
    account_id: str,
    channel: str,
    existing: NeurocommentReadiness | None,
    outcome: AccountChannelOnboarding,
    *,
    member: bool,
) -> None:
    """Log the two good outcomes of a join — each on its TRANSITION only.

    Every other ``neurocomment_onboard_*`` event is a refusal or a wait, so an approval
    and a pair going comment-able passed with nothing in the log but the gateway's join
    line, which reads exactly like an ordinary re-onboard.

    Both verdicts come from ``existing`` — the readiness row as it was BEFORE this join —
    because nothing else can tell a transition from a repeat: this function re-joins and
    re-writes ``ready`` on every pass, so a bare "pair is ready" would fire for every ready
    pair in the fleet on every operator Start, every campaign reconcile and every other
    channel's poke. That is the same flood the pending-request guard above was written to
    stop.

    ``join_request_attempts`` is 0 until a request actually goes out (and back to 0 once
    ``clear_join_request`` forgets one), so a non-zero count plus a join that made us a
    member is exactly "the admin approved it". A pair approved straight into a bot
    challenge logs the approval alone, which is right: it is in the group, just not
    comment-able yet.
    """
    if existing is not None and existing.join_request_attempts > 0 and member:
        await log_event(
            "INFO",
            "neurocomment_onboard_join_request_approved",
            account_id=account_id,
            extra={"channel": channel},
        )
    if outcome.state == "ready" and (existing is None or not existing.ready):
        await log_event(
            "INFO",
            "neurocomment_onboard_pair_ready",
            account_id=account_id,
            extra={"channel": channel},
        )


def _join_request_in_flight(readiness: NeurocommentReadiness, now: datetime) -> bool:
    """True while an approval request must NOT be re-sent for this pair.

    Two ways to be in flight: the next request is not due yet, or the pair has already
    used all its attempts (it then stays in flight forever — the sweep is what ends it,
    by dropping the channel).

    Due at ``first + attempts x retry_hours``, the exact complement of the sweep's own
    schedule in ``_sweep._review_join_requests`` — the sweep is what wakes onboarding for
    a retry, so the two must agree or a pass would undo the pacing the sweep asked for.
    ``join_requested_at`` is the FIRST request and never moves (the repository coalesces
    it), so a bare retry window here would authorize the next request the instant the
    FIRST one lapsed, however many had gone out since; only the shipped
    ``join_request_max_attempts=2`` and the attempts guard above kept that off the wire.
    """
    if readiness.join_requested_at is None:
        return False
    nc = settings.neurocomment
    if readiness.join_request_attempts >= nc.join_request_max_attempts:
        return True
    requested = datetime.fromisoformat(readiness.join_requested_at)
    due_after = timedelta(hours=nc.join_request_retry_hours * readiness.join_request_attempts)
    return now - requested < due_after


async def _resolve_linked_group(account_id: str, channel: str) -> LinkedDiscussionGroupResult:
    """Read the channel's linked discussion group and cache the resolution."""
    ensure_current()
    linked = await _seams.execute_read(account_id, GetLinkedDiscussionGroup(channel=channel))
    ensure_current()
    if not isinstance(linked, LinkedDiscussionGroupResult):  # pragma: no cover - typed gateway
        msg = f"Unexpected read result for {channel!r}: {type(linked).__name__}"
        raise TypeError(msg)
    await upsert_linked_group(
        channel,
        linked.linked_chat_id,
        comments_enabled=linked.comments_enabled,
    )
    if not linked.comments_enabled or linked.linked_chat_id is None:
        # A channel with no discussion group can never be commented on, and no readiness
        # row is written for it — so it looked un-onboarded rather than impossible, and
        # was re-resolved and re-reported on every pass forever. Handled here, the single
        # live-resolve site, so the campaign loop and the single-pair path both report
        # AND drop it; ``_comments_off`` owns what that means.
        await _comments_off.report_and_drop(channel, account_id)
    return linked


async def _safe_resolve(account_id: str, channel: str) -> LinkedDiscussionGroupResult | None:
    """Resolve+cache a channel's linked group; on any gateway failure, log and return None.

    ``execute_read`` *raises* (``TelegramReadError`` on flood/RPC, account-not-found,
    or a wrong type) rather than returning a typed error, so one channel's resolve
    must never abort the campaign loop — mirrors ``_join_pair_safely``.
    """
    try:
        return await _resolve_linked_group(account_id, channel)
    except Exception as exc:  # noqa: BLE001 - one channel must never abort the campaign
        await log_event(
            "ERROR",
            "neurocomment_onboard_resolve_failed",
            account_id=account_id,
            extra={"channel": channel, "error_type": type(exc).__name__},
        )
        return None


async def _at_join_cap(account_id: str) -> bool:
    """True when ``account_id`` has hit its rolling-24h channel-join cap (0 = no cap).

    Telegram freezes an account after ~20-50 channel joins a day, so both join sites
    gate on this before sending a real join RPC — an over-cap account has its
    remaining joins skipped this run and resumes as the 24h window rolls.

    The cap is the account's own when the operator has set one (#58), else the fleet
    setting; ``services._account_limits`` owns that choice for every join site.
    """
    cap = await account_join_cap(account_id, settings.neurocomment.max_joins_per_account_per_day)
    if cap <= 0:
        return False
    since = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    return await count_account_joins_since(account_id, since) >= cap


async def _daily_cap_outcome(account_id: str, channel: str) -> AccountChannelOnboarding:
    """Log the skipped join and hand back the non-terminal outcome for a full budget.

    One copy for the two places that refuse a join on the cap — the single-pair
    entrypoint above and the re-read inside the join mutex — so the board and the log
    cannot come to describe the same refusal differently. The campaign loop keeps its
    own copy in ``_onboard_channel``, where the same verdict also feeds a progress event.
    """
    await log_event(
        "WARNING",
        "neurocomment_join_daily_cap",
        account_id=account_id,
        extra={"channel": channel},
    )
    return AccountChannelOnboarding(
        account_id=account_id, channel=channel, state="joining", reason="daily_join_cap"
    )
