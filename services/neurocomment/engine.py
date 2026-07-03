"""Neurocomment on-post pipeline — the heart of the comment engine (issue #118).

A fresh post surfaced by the gateway listener flows through one
:func:`handle_new_post` call: map it to its campaign, filter out posts we don't
comment on, pick a healthy under-quota account, win the atomic claim, generate a
short on-prompt comment, run it through the light content checks, pause a human
beat, then post it and classify the outcome.

Load-bearing (not optional, even under ponytail): the atomic ``claim_comment``
idempotency gate (no double comments across concurrent events / restarts), the
account health/quota/cooldown selection gates (anti-ban), and the outer
try/except that isolates any fault from the listener task.

All Telegram / Gemini / spam / randomness access goes through ``_seams`` so a
test patches one place; the account-health reads are imported at module scope so
tests patch ``engine.<name>``. The reply delay uses ``asyncio.sleep`` (patched).
"""

from __future__ import annotations

import asyncio  # noqa: F401 - re-exported so tests can patch engine.asyncio.sleep (used by _generate).
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple

from core.db import (
    claim_comment,
    count_channel_comments_per_account_since,
    count_comments_per_account_since,
    fetch_active_campaign_for_channel,
    list_accounts,
    list_campaign_accounts,
    list_campaign_channels,
    list_campaign_readiness,
    list_device_fingerprints,
    list_spam_statuses,
    list_warming_states,
    mark_comment_failed,
)
from core.logging import log_event
from services.neurocomment import _filters, _seams, _state
from services.neurocomment.settings_store import load_settings as load_neuro_settings
from services.trust import account_trust_score_from
from services.warming.pacing import evaluate_readiness

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.device_fingerprint import DeviceFingerprint
    from schemas.neurocomment import NeurocommentCampaign, NeurocommentSettings
    from schemas.spam_status import SpamStatusVerdict
    from schemas.telegram_actions import NewPostEvent
    from schemas.warming import WarmingStateRecord


async def handle_new_post(event: NewPostEvent) -> None:
    """Comment on one fresh post, end-to-end. Never raises (listener-safe)."""
    try:
        await _handle_new_post(event)
    except Exception as exc:  # noqa: BLE001 - a fault must never kill the listener task.
        await log_event(
            "ERROR",
            "neurocomment_pipeline_failed",
            extra={
                "channel": event.channel,
                "post_id": event.post_id,
                "error_type": type(exc).__name__,
                "message": str(exc),
            },
        )


async def _handle_new_post(event: NewPostEvent) -> None:
    campaign = await fetch_active_campaign_for_channel(event.channel)
    if campaign is None:
        await log_event(
            "INFO",
            "neurocomment_no_campaign",
            extra={"channel": event.channel, "post_id": event.post_id},
        )
        return

    skip = _filters.filter_reason(event)
    if skip is not None:
        await log_event(
            "INFO",
            "neurocomment_post_skipped",
            extra={"channel": event.channel, "post_id": event.post_id, "reason": skip},
        )
        return

    now = datetime.now(UTC)
    if _state.channel_in_backoff(event.channel, now) or _state.is_channel_in_challenge_backoff(
        event.channel, now
    ):
        # Backed off — by the deletion sweep (mass deletions) or K solver failures
        # (#147). Skip before selection so we leave the channel alone until it expires.
        await log_event(
            "INFO",
            "neurocomment_channel_cooled",
            extra={"channel": event.channel, "post_id": event.post_id},
        )
        return

    account_id = await _select_account(campaign, event.channel)
    if account_id is None:
        await log_event(
            "INFO",
            "neurocomment_no_account_available",
            extra={"channel": event.channel, "post_id": event.post_id},
        )
        return

    won = await claim_comment(event.channel, event.post_id, campaign.campaign_id, account_id)
    if not won:
        # Another worker already owns this post — idempotency, no duplicate.
        return

    # The claim is won; from here any exit other than a delivered comment must release
    # the claim, or the row stays 'claimed' forever (post never commentable, quota
    # consumed for the window). A CancelledError on shutdown is cleaned up then re-raised
    # so the task still cancels; other faults are handled by the outer listener guard.
    try:
        await _generate_and_post(event, campaign, account_id)
    except BaseException:
        await mark_comment_failed(event.channel, event.post_id)
        raise


class _SelectionPool(NamedTuple):
    """Bulk-loaded signals to score every candidate in one pass (no per-account I/O)."""

    accounts: dict[str, AccountRead]
    ready_account_ids: frozenset[str]  # accounts ready for THIS channel
    states: dict[str, WarmingStateRecord]
    spam: dict[str, SpamStatusVerdict]
    fingerprints: dict[str, DeviceFingerprint]
    hourly_counts: dict[str, int]
    daily_counts: dict[str, int]
    limits: NeurocommentSettings  # operator-editable caps/min-trust (saved or config)


async def _load_selection_pool(campaign_id: str, channel: str, now: datetime) -> _SelectionPool:
    """Bulk-read every selection signal once (mirrors ``services.neurocomment.board``)."""
    limits = await load_neuro_settings()
    accounts = {acc.account_id: acc for acc in (await list_accounts()).accounts}
    ready_account_ids = frozenset(
        r.account_id
        for r in (await list_campaign_readiness(campaign_id)).readiness
        # Honour the operator skip (#148) even if a stale re-enable left ready=1: a
        # human-skipped pair is never selected.
        if r.channel == channel and r.ready and not r.human_skipped
    )
    states = {rec.account_id: rec for rec in await list_warming_states()}
    spam = await list_spam_statuses()
    fingerprints = await list_device_fingerprints()

    hour_ago = (now - timedelta(hours=1)).isoformat()
    hourly_rows = (await count_comments_per_account_since(hour_ago)).counts
    hourly = {c.account_id: c.count for c in hourly_rows}
    daily: dict[str, int] = {}
    if limits.max_comments_per_channel_per_day > 0:
        day_ago = (now - timedelta(days=1)).isoformat()
        daily_rows = (await count_channel_comments_per_account_since(channel, day_ago)).counts
        daily = {c.account_id: c.count for c in daily_rows}
    return _SelectionPool(
        accounts=accounts,
        ready_account_ids=ready_account_ids,
        states=states,
        spam=spam,
        fingerprints=fingerprints,
        hourly_counts=hourly,
        daily_counts=daily,
        limits=limits,
    )


async def _select_account(campaign: NeurocommentCampaign, channel: str) -> str | None:
    """Pick one ready, healthy, under-quota, non-cooled account at random.

    Every signal is bulk-loaded once (mirroring ``services.neurocomment.board``), so
    selection scores N candidates from a handful of queries instead of ~7 per
    account — the cost stays flat as the fleet grows. Spam status is read from the
    cache and never re-probed here: probing @SpamBot per post is itself a ban
    signal, so warming/onboarding own spam freshness.
    """
    # A pinned account (link.channel set) is eligible only for its channel; an
    # unpinned account (None) is eligible for every channel of the campaign.
    account_ids = [
        link.account_id
        for link in (await list_campaign_accounts(campaign.campaign_id)).links
        if link.channel in (None, channel)
    ]
    if not account_ids:
        return None
    channel_count = max(1, len((await list_campaign_channels(campaign.campaign_id)).links))
    now = datetime.now(UTC)
    pool = await _load_selection_pool(campaign.campaign_id, channel, now)
    candidates = [
        account_id
        for account_id in account_ids
        if _is_eligible(account_id, channel, channel_count, now, pool)
    ]
    if not candidates:
        return None
    return _seams.rng.choice(candidates)


def _is_eligible(
    account_id: str,
    channel: str,
    channel_count: int,
    now: datetime,
    pool: _SelectionPool,
) -> bool:
    if _state.in_cooldown(account_id, now, channel):
        return False
    if account_id not in pool.ready_account_ids:
        return False
    account = pool.accounts.get(account_id)
    if account is None:
        return False
    if not _is_healthy(account, channel_count, now, pool):
        return False
    return _under_quota(account_id, pool)


def _is_healthy(
    account: AccountRead,
    channel_count: int,
    now: datetime,
    pool: _SelectionPool,
) -> bool:
    """Warming readiness gate + Trust Score, scored from already-loaded signals."""
    spam = pool.spam.get(account.account_id)
    fingerprint = pool.fingerprints.get(account.account_id)
    trust = account_trust_score_from(
        account=account,
        record=pool.states.get(account.account_id),
        spam=spam,
        lang_code=fingerprint.system_lang_code if fingerprint else None,
        now=now,
    )
    if trust.score < pool.limits.min_trust_score:
        return False
    health = evaluate_readiness(account, channel_count, spam=spam, trust_score=trust)
    return health.ready


def _under_quota(account_id: str, pool: _SelectionPool) -> bool:
    # Quota counts in-flight claims AND delivered comments (status in claimed/posted),
    # so a burst arriving inside one account's reply-delay window can't stack past the
    # cap — each claim consumes quota the moment it is won.
    # ponytail: a sub-millisecond race still exists in the select->claim gap; a
    # per-account asyncio.Lock would close it fully if it ever bites.
    day_cap = pool.limits.max_comments_per_channel_per_day
    over_hour = pool.hourly_counts.get(account_id, 0) >= pool.limits.max_comments_per_hour
    over_day = day_cap > 0 and pool.daily_counts.get(account_id, 0) >= day_cap
    return not (over_hour or over_day)


# The generation + outcome-classification back half lives in ``_generate`` (file-
# size budget). ``handle_new_post`` calls ``_generate_and_post`` below; the rest
# are re-exported so ``services.neurocomment.engine.<name>`` still resolves.
from services.neurocomment._generate import (  # noqa: E402, F401 - re-export after the module body.
    _COOLDOWN_STATUSES,
    _GATE_ERRORS,
    _apply_cooldown,
    _build_request,
    _classify_post,
    _generate_acceptable,
    _generate_and_post,
    _recent_channel_comments,
    _register_challenge_failure,
)
