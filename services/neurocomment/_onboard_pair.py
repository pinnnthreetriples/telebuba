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
    stamp_rejoin_attempt,
    upsert_linked_group,
    upsert_readiness,
)
from core.logging import log_event
from schemas.neurocomment import AccountChannelOnboarding, NeurocommentReadiness
from schemas.telegram_actions import (
    GetLinkedDiscussionGroup,
    JoinDiscussionGroup,
    LinkedDiscussionGroupResult,
)
from services.neurocomment import _rejoin, _seams, _state

# The join ActionResult → OnboardingState mapping + solver recording live in
# ``_classify`` (file-size cap); ``_join_and_classify`` below delegates to it.
from services.neurocomment._classify import _classify_join


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
    # Rolling-24h join cap (anti-freeze): both operator single-pair paths
    # (direct call + retry_pair) funnel through here, so the gate lives here to
    # cover them — the campaign loop gates in _onboard_pair before its jitter
    # sleep. At cap: skip the join RPC (no record), retry once the window rolls.
    # Non-terminal "joining" so the pair is reconsidered, not stuck.
    if await _at_join_cap(account_id):
        await log_event(
            "WARNING",
            "neurocomment_join_daily_cap",
            account_id=account_id,
            extra={"channel": channel},
        )
        return AccountChannelOnboarding(
            account_id=account_id, channel=channel, state="joining", reason="daily_join_cap"
        )
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

    An operator-skipped pair (#148) or an auto-banned pair (#30) is left alone:
    re-joining would run the solver and flip readiness back to ready, undoing the
    skip / reviving the ban. Cleared via ``retry_pair`` (skip) or a can_send probe (ban).

    A pair with an approval request still in flight is left alone the same way, and sits
    next to those guards for the same reason: both must cost zero join RPCs. An admin
    reads a re-request as spam, and every pass used to send one.
    """
    existing = await fetch_readiness(account_id, channel)
    if existing is not None and (existing.human_skipped or existing.banned):
        state = "human_skipped" if existing.human_skipped else "banned"
        return AccountChannelOnboarding(account_id=account_id, channel=channel, state=state)
    if existing is not None and _join_request_in_flight(existing, datetime.now(UTC)):
        # One line per pair per pass, and only for pairs actually held back — the old
        # behaviour logged a fresh join_by_request every pass *and* paid the RPC for it.
        await log_event(
            "INFO",
            "neurocomment_onboard_join_request_pending",
            account_id=account_id,
            extra={"channel": channel, "attempts": existing.join_request_attempts},
        )
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="join_by_request",
        )
    if _state.channel_paused(await fetch_channel_paused_until(channel), datetime.now(UTC)):
        await upsert_readiness(account_id, channel, joined=False, captcha_passed=False, ready=False)
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="channel_paused",
        )
    if existing is not None and _rejoin.access_lost(existing):
        if not _rejoin.retry_due(existing, datetime.now(UTC)):
            # A pair that lost chat access gets one re-join a day, four in total (#43).
            # Held back here and not only in the sweep, because every operator Start and
            # every campaign reconcile starts a pass too — without this guard a busy day
            # would spend all four attempts in an hour. ``retry_pair`` (which deletes the
            # row) is the way past it, exactly as for a pending join request.
            return AccountChannelOnboarding(
                account_id=account_id,
                channel=channel,
                state="joining",
                reason="rejoin_backoff",
            )
        # Stamped BEFORE the RPC: a re-join that fails hard re-writes the same sentinel
        # row, so a stamp written on the success path only would let the pair retry every
        # pass forever — the very loop this rule exists to bound.
        await stamp_rejoin_attempt(account_id, channel)
    result = await _seams.execute(account_id, JoinDiscussionGroup(channel=channel))
    if result.status == "ok":
        # A real join RPC landed → count it against the account's rolling-24h cap.
        # ``already_participant`` is a no-op re-join (still a success below) and must
        # NOT be recorded, else a re-onboard would inflate the cap with joins that
        # never happened.
        await record_join(account_id)
    return await _classify_join(
        account_id, channel, result, group_id, solver_enabled=solver_enabled
    )


def _join_request_in_flight(readiness: NeurocommentReadiness, now: datetime) -> bool:
    """True while an approval request must NOT be re-sent for this pair.

    Two ways to be in flight: the last request is younger than the retry window, or the
    pair has already used all its attempts (it then stays in flight forever — the sweep
    is what ends it, by dropping the channel).
    """
    if readiness.join_requested_at is None:
        return False
    nc = settings.neurocomment
    if readiness.join_request_attempts >= nc.join_request_max_attempts:
        return True
    requested = datetime.fromisoformat(readiness.join_requested_at)
    return now - requested < timedelta(hours=nc.join_request_retry_hours)


async def _resolve_linked_group(account_id: str, channel: str) -> LinkedDiscussionGroupResult:
    """Read the channel's linked discussion group and cache the resolution."""
    linked = await _seams.execute_read(account_id, GetLinkedDiscussionGroup(channel=channel))
    if not isinstance(linked, LinkedDiscussionGroupResult):  # pragma: no cover - typed gateway
        msg = f"Unexpected read result for {channel!r}: {type(linked).__name__}"
        raise TypeError(msg)
    await upsert_linked_group(
        channel,
        linked.linked_chat_id,
        comments_enabled=linked.comments_enabled,
    )
    if not linked.comments_enabled or linked.linked_chat_id is None:
        # A channel with no discussion group can never be commented on, and this was
        # the one dead end with NO log trace at all: the state lands in
        # ``neurocomment_linked_groups`` and no readiness row is written, so the
        # channel looked un-onboarded rather than impossible. Logged here (the single
        # live-resolve site) so both the campaign loop and the single-pair path report it.
        await log_event(
            "INFO",
            "neurocomment_channel_comments_off",
            account_id=account_id,
            extra={"channel": channel},
        )
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
    """
    cap = settings.neurocomment.max_joins_per_account_per_day
    if cap <= 0:
        return False
    since = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    return await count_account_joins_since(account_id, since) >= cap
