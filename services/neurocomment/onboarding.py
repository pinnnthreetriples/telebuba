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
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from core.config import settings
from core.db import (
    fetch_active_campaign_for_channel,
    fetch_campaign,
    fetch_readiness,
    list_campaign_accounts,
    list_campaign_channels,
    list_campaign_readiness,
    mark_pair_banned,
    upsert_linked_group,
    upsert_readiness,
)
from core.logging import log_event
from schemas.neurocomment import AccountChannelOnboarding, CampaignOnboardingResult, OnboardingState
from schemas.neurocomment_progress import OnboardingProgressEvent
from schemas.telegram_actions import (
    ActionResult,
    GetLinkedDiscussionGroup,
    JoinDiscussionGroup,
    LinkedDiscussionGroupResult,
)
from services.neurocomment import _seams, _state, challenge
from services.neurocomment._onboard_channel import OnboardContext, onboard_channel

# Writes Telegram-blocked at join → chat_restricted (Ф2 #120); solver can't clear it.
_GATE_ERRORS = frozenset({"ChatGuestSendForbiddenError", "ChatWriteForbiddenError"})
# A hard ban at join → sticky ban (#30), same as a ban hit while commenting (never retried).
_BAN_ERROR = "UserBannedInChannelError"
# Rate-limit families: never terminal, retry later, must return promptly.
_RETRY_STATUSES = frozenset({"flood_wait", "slow_mode_wait", "premium_wait", "peer_flood"})


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


async def _classify_join(
    account_id: str,
    channel: str,
    result: ActionResult,
    group_id: int,
    *,
    solver_enabled: bool,
) -> AccountChannelOnboarding:
    """Map a join ``ActionResult`` to a state + persisted readiness row."""
    if result.status == "ok":
        # Joined → run the proactive challenge solver before declaring the pair
        # comment-able (Ф2 #145), unless the solver is disabled for this campaign.
        return await _solve_and_record(account_id, channel, group_id, solver_enabled=solver_enabled)
    if result.status in _RETRY_STATUSES:
        # Non-terminal: do not write ready; surface the wait so the account is
        # retried later instead of getting stuck. Return promptly (no sleep).
        await log_event(
            "INFO",
            "neurocomment_onboard_retry_later",
            account_id=account_id,
            extra={"channel": channel, "status": result.status},
        )
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="joining",
            reason=result.error_type or f"{result.status}:{result.flood_wait_seconds}",
        )
    if result.error_type == "InviteRequestSentError":
        await upsert_readiness(account_id, channel, joined=False, captcha_passed=False, ready=False)
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="join_by_request",
        )
    if result.error_type == _BAN_ERROR:
        # Hard ban at join → sticky ban (#30) so a re-onboard stops re-joining the group.
        await upsert_readiness(account_id, channel, joined=True, captcha_passed=False, ready=False)
        await mark_pair_banned(account_id, channel)
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="banned",
        )
    if result.error_type in _GATE_ERRORS:
        # Telegram-level write block (mute / restrict) → chat_restricted (Ф2 #120).
        # Unsolvable by the challenge solver, so it is never invoked here; joined stays
        # True (we are a member) but ready is False.
        await upsert_readiness(account_id, channel, joined=True, captcha_passed=False, ready=False)
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="chat_restricted",
        )
    # Hard failure (invalid invite / banned / private): never joined and never will
    # without operator action. Persist a signal distinct from the approval-gate row
    # (which is also joined=False) so the board renders join_failed, not "awaiting
    # approval": captcha_passed=True on an unjoined row is the sentinel (no other path
    # produces that combination). ready stays False so the pair is never selected.
    await upsert_readiness(account_id, channel, joined=False, captcha_passed=True, ready=False)
    return AccountChannelOnboarding(
        account_id=account_id,
        channel=channel,
        state="failed",
        reason=result.error_type or result.error_message,
    )


async def _solve_and_record(
    account_id: str,
    channel: str,
    group_id: int,
    *,
    solver_enabled: bool,
) -> AccountChannelOnboarding:
    """Run the challenge solver on a freshly-joined group; persist the readiness.

    With the solver disabled (opt-in, #148) an ok join is assumed comment-able →
    ``ready``. Otherwise ``give_up`` / ``failed`` (a detected/unsolved challenge)
    leaves the pair not-ready — the audit row drives the board's ``bot_challenge``;
    ``no_challenge`` / ``solved`` means comment-able → ``ready``.
    """

    def _result(state: OnboardingState) -> AccountChannelOnboarding:
        return AccountChannelOnboarding(account_id=account_id, channel=channel, state=state)

    if not solver_enabled:
        await upsert_readiness(account_id, channel, joined=True, captcha_passed=True, ready=True)
        return _result("ready")
    outcome = await challenge.solve_if_present(account_id, channel, group_id)
    if outcome == "rate_limited":
        # LLM gateway 429'd: transient, not a solver failure — write no readiness and
        # surface a retry-later state so the pair is re-onboarded later, un-penalized
        # (no bot_challenge, no #147 channel back-off).
        return _result("joining").model_copy(update={"reason": "llm_rate_limited"})
    if outcome in ("give_up", "failed"):
        # Detected but unsolved (or the click errored) → not comment-able; the
        # solver's audit row is what the board reads to render bot_challenge.
        await upsert_readiness(account_id, channel, joined=True, captcha_passed=False, ready=False)
        return _result("bot_challenge")
    # no_challenge, or solved (click dispatched, audit pending) → optimistically
    # comment-able; the engine confirms a solved click on the first comment.
    await upsert_readiness(account_id, channel, joined=True, captcha_passed=True, ready=True)
    return _result("ready")


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
