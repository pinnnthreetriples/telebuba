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
- ``captcha_gated``  — joined but writes are forbidden → treated as a gate.
- ``joining``        — rate-limited; retry later, account not stuck.
- ``failed``         — any other error.

All Telegram and randomness access goes through ``_seams`` so tests patch one
place; the inter-join sleep uses ``asyncio.sleep`` (patched in tests).
"""

from __future__ import annotations

import asyncio

from core.config import settings
from core.db import (
    fetch_campaign,
    list_campaign_accounts,
    list_campaign_channels,
    upsert_linked_group,
    upsert_readiness,
)
from core.logging import log_event
from schemas.neurocomment import AccountChannelOnboarding, CampaignOnboardingResult
from schemas.telegram_actions import (
    ActionResult,
    GetLinkedDiscussionGroup,
    JoinDiscussionGroup,
    LinkedDiscussionGroupResult,
)
from services.neurocomment import _seams

# Join failed because writes are blocked → treat as a captcha/gate signal we
# detect and skip (not solve). The set is small and intentional.
_GATE_ERRORS = frozenset(
    {"ChatGuestSendForbiddenError", "ChatWriteForbiddenError", "UserBannedInChannelError"},
)
# Rate-limit families: never terminal, retry later, must return promptly.
_RETRY_STATUSES = frozenset({"flood_wait", "slow_mode_wait", "premium_wait", "peer_flood"})


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
    if not linked.comments_enabled:
        # comments_off is a channel property, not a per-account state, so we
        # record no readiness row — the campaign loop also short-circuits it.
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="comments_off",
        )
    return await _join_and_classify(account_id, channel)


async def _join_and_classify(account_id: str, channel: str) -> AccountChannelOnboarding:
    """Join the (already-resolved, comment-enabled) group and persist readiness."""
    result = await _seams.execute(account_id, JoinDiscussionGroup(channel=channel))
    return await _classify_join(account_id, channel, result)


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
) -> AccountChannelOnboarding:
    """Map a join ``ActionResult`` to a state + persisted readiness row."""
    if result.status == "ok":
        # ponytail (#120): no entry-captcha probe exists yet, so an ok join is
        # assumed comment-able. #118 lazily flips captcha when a comment fails.
        await upsert_readiness(account_id, channel, joined=True, captcha_passed=True, ready=True)
        return AccountChannelOnboarding(account_id=account_id, channel=channel, state="ready")
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
    if result.error_type in _GATE_ERRORS:
        # Rare join-time gate: joined, but writes forbidden → captcha/gate signal.
        # NOTE: a plain join almost never surfaces these; primary entry-captcha
        # detection is lazy, at comment time, in the engine (#118). We do NOT probe.
        await upsert_readiness(account_id, channel, joined=True, captcha_passed=False, ready=False)
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="captcha_gated",
        )
    await upsert_readiness(account_id, channel, joined=False, captcha_passed=False, ready=False)
    return AccountChannelOnboarding(
        account_id=account_id,
        channel=channel,
        state="failed",
        reason=result.error_type or result.error_message,
    )


async def onboard_campaign(campaign_id: str) -> CampaignOnboardingResult:
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
    accounts = [link.account_id for link in (await list_campaign_accounts(campaign_id)).links]
    # Establish each serving account's spam verdict up front. Selection reads this
    # from cache and never re-probes @SpamBot per post (anti-ban), so a verdict must
    # exist before the account goes on the line.
    await _probe_account_spam(accounts)

    outcomes: list[AccountChannelOnboarding] = []
    joined_once = False
    for channel_link in channels:
        channel = channel_link.channel
        # Resolve the linked group ONCE per channel (anti-ban + fewer reads).
        comments_on = await _channel_comments_enabled(accounts, channel, outcomes)
        if not comments_on:
            continue
        for account_id in accounts:
            if joined_once:
                await asyncio.sleep(_join_jitter_seconds())
            outcomes.append(await _join_pair_safely(account_id, channel))
            joined_once = True
    return CampaignOnboardingResult(campaign_id=campaign_id, outcomes=outcomes)


async def _channel_comments_enabled(
    accounts: list[str],
    channel: str,
    outcomes: list[AccountChannelOnboarding],
) -> bool:
    """Resolve+cache the channel's group once; record per-account skips, return joinable?.

    Uses the first account's session for the read (any member-less read works). A
    resolve failure records a ``failed`` outcome per account; comments-off records a
    ``comments_off`` outcome per account. Either way returns ``False`` so the caller
    skips the joins — one bad channel never aborts the campaign.
    """
    if not accounts:
        return False
    linked = await _safe_resolve(accounts[0], channel)
    if linked is None:
        outcomes.extend(
            AccountChannelOnboarding(
                account_id=account_id, channel=channel, state="failed", reason="resolve_failed"
            )
            for account_id in accounts
        )
        return False
    if linked.comments_enabled:
        return True
    outcomes.extend(
        AccountChannelOnboarding(account_id=account_id, channel=channel, state="comments_off")
        for account_id in accounts
    )
    return False


def _join_jitter_seconds() -> float:
    """Jittered anti-ban pause between discussion-group joins (config-driven)."""
    nc = settings.neurocomment
    return _seams.rng.uniform(nc.join_delay_min_seconds, nc.join_delay_max_seconds)


async def _join_pair_safely(account_id: str, channel: str) -> AccountChannelOnboarding:
    """Join one pair, converting an unexpected raise into a ``failed`` outcome."""
    try:
        return await _join_and_classify(account_id, channel)
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


async def _probe_account_spam(accounts: list[str]) -> None:
    """Probe each serving account's spam status once at onboarding (off the post path).

    Selection reads the cached verdict and never re-probes @SpamBot per post (anti-ban),
    so onboarding establishes one up front. ``force=False`` reuses a fresh cache; a probe
    failure is logged, never fatal — onboarding proceeds.
    """
    for account_id in accounts:
        try:
            await _seams.refresh_spam_status(account_id, force=False)
        except Exception as exc:  # noqa: BLE001 - a spam probe must never abort onboarding
            await log_event(
                "WARNING",
                "neurocomment_onboard_spam_probe_failed",
                account_id=account_id,
                extra={"error_type": type(exc).__name__},
            )
