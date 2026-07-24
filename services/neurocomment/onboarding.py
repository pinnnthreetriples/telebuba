"""Campaign pre-onboarding — prepare accounts so a fresh post is commentable.

For each (account, channel) we resolve the channel's linked discussion group,
join it, and persist readiness, so the hot path (a brand-new post) never pays
the join cost. Captcha is *detect-and-skip only* here — solving entry captchas
is deferred to spike #120; the comment engine (#118) lazily marks captcha when
a comment is actually forbidden.

Outcome states (``OnboardingState``):
- ``ready``          — joined and (MVP) assumed comment-able.
- ``comments_off``   — channel has comments disabled; nothing to join.
- ``join_by_request``— group is approval-gated; request sent, not a member.
- ``chat_restricted``— joined but writes are Telegram-blocked (mute/restrict/ban).
- ``joining``        — rate-limited; retry later, account not stuck.
- ``failed``         — any other error.

All Telegram and randomness access goes through ``_seams`` so tests patch one
place; the inter-join sleep uses ``asyncio.sleep`` (patched in tests).
"""

from __future__ import annotations

import asyncio  # noqa: F401 - re-exported so tests can patch onboarding.asyncio.sleep
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from core.config import settings
from core.db import (
    count_account_joins_since,
    fetch_active_campaign_for_channel,
    fetch_campaign,
    fetch_readiness,
    list_campaign_accounts,
    list_campaign_channels,
    list_campaign_readiness,
    record_join,
    upsert_linked_group,
    upsert_readiness,
)
from core.logging import log_event
from schemas.neurocomment import AccountChannelOnboarding, CampaignOnboardingResult
from schemas.neurocomment_progress import OnboardingProgressEvent
from schemas.telegram_actions import (
    GetLinkedDiscussionGroup,
    JoinDiscussionGroup,
    LinkedDiscussionGroupResult,
)
from services.neurocomment import _seams, _state
from services.neurocomment._onboard_channel import OnboardContext, onboard_channel


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

    A channel in challenge back-off (#147, K solver failures) is left alone — no
    join, no solver — until its cooldown expires; the board renders it
    ``bot_challenge_backoff`` from the in-memory back-off state.

    An operator-skipped pair (#148) or an auto-banned pair (#30) is left alone:
    re-joining would run the solver and flip readiness back to ready, undoing the
    skip / reviving the ban. Cleared via ``retry_pair`` (skip) or a can_send probe (ban).
    """
    existing = await fetch_readiness(account_id, channel)
    if existing is not None and (existing.human_skipped or existing.banned):
        state = "human_skipped" if existing.human_skipped else "banned"
        return AccountChannelOnboarding(account_id=account_id, channel=channel, state=state)
    if _state.is_channel_in_challenge_backoff(channel, datetime.now(UTC)):
        await upsert_readiness(account_id, channel, joined=False, captcha_passed=False, ready=False)
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="bot_challenge_backoff",
        )
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


async def onboard_campaign(
    campaign_id: str,
    *,
    on_progress: Callable[[OnboardingProgressEvent], None] | None = None,
) -> CampaignOnboardingResult:
    """Onboard every (account, channel) pair of a campaign with paced joins.

    Resolves each channel's linked group once: a comments-off channel records
    one ``comments_off`` outcome per account and is never joined. Between real
    joins we sleep a jittered ``rng.uniform(join_delay_min, join_delay_max)``
    for anti-ban pacing. A single failing pair is logged and skipped — the loop
    never raises.
    """
    campaign = await fetch_campaign(campaign_id)
    if campaign is None:
        return CampaignOnboardingResult(campaign_id=campaign_id)

    channels = (await list_campaign_channels(campaign_id)).links
    account_links = (await list_campaign_accounts(campaign_id)).links
    accounts = [link.account_id for link in account_links]
    # An account with a channel subset onboards ONLY against those channels; an
    # empty subset keeps the all-channels behaviour (accounts_for).
    pins = {link.account_id: link.channels for link in account_links}
    solver_enabled = _effective_solver_enabled(campaign.solver_enabled)

    # Pairs already onboarded (joined + ready + captcha-passed, not operator-skipped)
    # get a fast "already ready" outcome and skip the join + jitter sleep. Lets Start
    # re-run onboarding cheaply: a fully-prepared campaign costs one read, not minutes.
    # ponytail: a campaign solver_enabled toggle does NOT re-onboard already-ready
    # pairs — captcha_passed was recorded when the previous setting was applied.
    # Operators must use services.neurocomment.retry_pair to invalidate per-pair
    # readiness after flipping the toggle.
    already_ready = {
        (r.account_id, r.channel)
        for r in (await list_campaign_readiness(campaign_id)).readiness
        if r.ready and r.joined and r.captcha_passed and not r.human_skipped and not r.banned
    }

    def report(event: OnboardingProgressEvent) -> None:
        if on_progress:
            on_progress(event)

    report(
        OnboardingProgressEvent(
            code="onboarding_started",
            account_count=len(accounts),
            channel_count=len(channels),
        )
    )

    # Establish each serving account's spam verdict up front. Selection reads this
    # from cache and never re-probes @SpamBot per post (anti-ban), so a verdict must
    # exist before the account goes on the line.
    await _probe_account_spam(accounts, report=on_progress)

    ctx = OnboardContext(
        accounts=accounts,
        already_ready=already_ready,
        outcomes=[],
        solver_enabled=solver_enabled,
        on_progress=on_progress,
        report=report,
        pins=pins,
    )
    joined_once = False
    for channel_link in channels:
        joined_once = await onboard_channel(channel_link.channel, ctx, joined_once=joined_once)
    ready_count = sum(1 for o in ctx.outcomes if o.state == "ready")
    report(
        OnboardingProgressEvent(
            code="onboarding_finished",
            ready_count=ready_count,
            total_count=len(ctx.outcomes),
        )
    )
    return CampaignOnboardingResult(campaign_id=campaign_id, outcomes=ctx.outcomes)


async def _resolve_group_for_join(
    accounts: list[str],
    channel: str,
    outcomes: list[AccountChannelOnboarding],
    report: Callable[[OnboardingProgressEvent], None] | None = None,
) -> int | None:
    """Resolve+cache the channel's group once; record per-account skips, return its id.

    Tries each account's session in order and uses the first that resolves — a single
    dead/banned session must not block the healthy accounts behind it. A resolve failure
    (every account failed) records a ``failed`` outcome per account; comments-off records a
    ``comments_off`` outcome per account. Either way returns ``None`` so the caller
    skips the joins — one bad channel never aborts the campaign.
    """
    if not accounts:
        return None
    linked = None
    for account_id in accounts:
        linked = await _safe_resolve(account_id, channel)
        if linked is not None:
            break
    if linked is None:
        if report:
            report(OnboardingProgressEvent(code="channel_resolve_failed", channel=channel))
        outcomes.extend(
            AccountChannelOnboarding(
                account_id=account_id, channel=channel, state="failed", reason="resolve_failed"
            )
            for account_id in accounts
        )
        return None
    if linked.comments_enabled and linked.linked_chat_id is not None:
        if report:
            report(OnboardingProgressEvent(code="channel_resolved", channel=channel))
        return linked.linked_chat_id
    if report:
        report(OnboardingProgressEvent(code="channel_comments_off", channel=channel))
    outcomes.extend(
        AccountChannelOnboarding(account_id=account_id, channel=channel, state="comments_off")
        for account_id in accounts
    )
    return None


def _join_jitter_seconds() -> float:
    """Jittered anti-ban pause between discussion-group joins (config-driven)."""
    nc = settings.neurocomment
    return _seams.rng.uniform(nc.join_delay_min_seconds, nc.join_delay_max_seconds)


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


async def _join_pair_safely(
    account_id: str,
    channel: str,
    group_id: int,
    *,
    solver_enabled: bool,
) -> AccountChannelOnboarding:
    """Join one pair, converting an unexpected raise into a ``failed`` outcome."""
    try:
        return await _join_and_classify(
            account_id, channel, group_id, solver_enabled=solver_enabled
        )
    except Exception as exc:  # noqa: BLE001 - one pair must never abort the campaign
        await log_event(
            "ERROR",
            "neurocomment_onboard_pair_failed",
            account_id=account_id,
            extra={"channel": channel, "error_type": type(exc).__name__},
        )
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="failed",
            reason=type(exc).__name__,
        )


async def _probe_account_spam(
    accounts: list[str],
    report: Callable[[OnboardingProgressEvent], None] | None = None,
) -> None:
    """Probe each serving account's spam status once at onboarding (off the post path).

    Selection reads the cached verdict and never re-probes @SpamBot per post (anti-ban),
    so onboarding establishes one up front. ``force=False`` reuses a fresh cache; a probe
    failure is logged, never fatal — onboarding proceeds.
    """
    for account_id in accounts:
        if report:
            report(OnboardingProgressEvent(code="spam_probe_started", account_id=account_id))
        try:
            await _seams.refresh_spam_status(account_id, force=False)
        except Exception as exc:  # noqa: BLE001 - a spam probe must never abort onboarding
            await log_event(
                "WARNING",
                "neurocomment_onboard_spam_probe_failed",
                account_id=account_id,
                extra={"error_type": type(exc).__name__},
            )
            if report:
                report(OnboardingProgressEvent(code="spam_probe_failed", account_id=account_id))


# The join-outcome classification + solver recording live in ``_classify`` (file-size
# cap); ``_join_and_classify`` above calls ``_classify_join``. Re-exported so that call
# and ``onboarding._classify_join`` / ``onboarding._GATE_ERRORS`` still resolve.
from services.neurocomment._classify import (  # noqa: E402, F401 - re-export after the module body.
    _BAN_ERROR,
    _GATE_ERRORS,
    _RETRY_STATUSES,
    _classify_join,
    _solve_and_record,
)
