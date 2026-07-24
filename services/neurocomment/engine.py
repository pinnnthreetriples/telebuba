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

import asyncio  # also re-exported so tests can patch engine.asyncio.sleep (used by _generate).
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple

from core.db import (
    claim_comment,
    count_account_channel_comments_since,
    count_account_comments_since,
    count_channel_comments_per_account_since,
    count_comments_per_account_since,
    fetch_active_campaign_for_channel,
    list_accounts_by_ids,
    list_campaign_accounts,
    list_campaign_channels,
    list_campaign_readiness,
    list_device_fingerprints_by_ids,
    list_spam_statuses_by_ids,
    list_warming_states_by_ids,
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


# One lock per account serialises its [re-read quota → claim] section so a burst of
# concurrent events for the same account can't each read an under-cap count and all
# claim (the bulk select->claim gap is otherwise racy — see _quota_block_reason). Single
# loop + single worker, so a plain dict needs no lock to grow (asyncio.Lock binds to
# the running loop; tests clear this between cases).
_ACCOUNT_LOCKS: dict[str, asyncio.Lock] = {}


def _account_lock(account_id: str) -> asyncio.Lock:
    lock = _ACCOUNT_LOCKS.get(account_id)
    if lock is None:
        lock = _ACCOUNT_LOCKS[account_id] = asyncio.Lock()
    return lock


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

    # Read the operator-editable limits ONCE per post (a DB read via to_thread) and
    # thread them through selection, the under-lock re-check, and the reply delay. The
    # caps can't change within one post, and reading once here preserves the "an override
    # takes effect on the next post" guarantee.
    limits = await load_neuro_settings()

    selection = await _select_account(campaign, event.channel, limits)
    account_id = selection.account_id
    if account_id is None:
        await log_event(
            "INFO",
            "neurocomment_no_account_available",
            extra={"channel": event.channel, "post_id": event.post_id, "reason": selection.reason},
        )
        return

    async with _account_lock(account_id):
        # H1: re-verify the chosen account with fresh counts under its own lock (a
        # concurrent burst may have claimed since selection), then claim — both inside
        # the lock so a serialized sibling sees the prior claim's row and can't stack
        # past the cap. ponytail: if the re-check finds the account now over-cap we drop
        # the post rather than re-selecting another (rare burst edge; not worth a loop).
        quota_reason = await _account_quota_block_reason(account_id, event.channel, limits)
        if quota_reason is not None:
            await log_event(
                "INFO",
                "neurocomment_no_account_available",
                extra={"channel": event.channel, "post_id": event.post_id, "reason": quota_reason},
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
        await _generate_and_post(event, campaign, account_id, limits)
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


async def _load_selection_pool(
    campaign_id: str,
    channel: str,
    account_ids: list[str],
    now: datetime,
    limits: NeurocommentSettings,
) -> _SelectionPool:
    """Bulk-read every selection signal once (mirrors ``services.neurocomment.board``).

    Signals are read only for ``account_ids`` — the campaign's channel-eligible
    candidates — so per-post cost is O(candidates), not O(fleet), as accounts scale.
    """
    accounts = {acc.account_id: acc for acc in (await list_accounts_by_ids(account_ids)).accounts}
    ready_account_ids = frozenset(
        r.account_id
        for r in (await list_campaign_readiness(campaign_id)).readiness
        # Honour the operator skip (#148) and the auto-ban (#30) even if a stale
        # re-enable left ready=1: a human-skipped or banned pair is never selected.
        if r.channel == channel and r.ready and not r.human_skipped and not r.banned
    )
    states = {rec.account_id: rec for rec in await list_warming_states_by_ids(account_ids)}
    spam = await list_spam_statuses_by_ids(account_ids)
    fingerprints = await list_device_fingerprints_by_ids(account_ids)

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


class _Selection(NamedTuple):
    """The chosen account, or ``None`` with the binding reason nothing was eligible."""

    account_id: str | None
    reason: str | None  # set only when account_id is None (surfaced in the miss log)


async def _select_account(
    campaign: NeurocommentCampaign, channel: str, limits: NeurocommentSettings
) -> _Selection:
    """Pick one ready, healthy, under-quota, non-cooled account at random.

    Every signal is bulk-loaded once (mirroring ``services.neurocomment.board``), so
    selection scores N candidates from a handful of queries instead of ~7 per
    account — the cost stays flat as the fleet grows. Spam status is read from the
    cache and never re-probed here: probing @SpamBot per post is itself a ban
    signal, so warming/onboarding own spam freshness.

    On a miss returns the binding blocker (``_selection_block_reason``) so the
    activity log can tell the operator *why* — busy quota vs cooldown vs not warmed.
    """
    # An account with a channel subset is eligible only for channels in it; an
    # account with an empty subset is eligible for every channel of the campaign.
    account_ids = [
        link.account_id
        for link in (await list_campaign_accounts(campaign.campaign_id)).links
        if not link.channels or channel in link.channels
    ]
    if not account_ids:
        return _Selection(None, "no_accounts_linked")
    channel_count = max(1, len((await list_campaign_channels(campaign.campaign_id)).links))
    now = datetime.now(UTC)
    pool = await _load_selection_pool(campaign.campaign_id, channel, account_ids, now, limits)
    candidates = [
        account_id
        for account_id in account_ids
        if _is_eligible(account_id, channel, channel_count, now, pool)
    ]
    if not candidates:
        return _Selection(
            None, _selection_block_reason(account_ids, channel, channel_count, now, pool)
        )
    return _Selection(_seams.rng.choice(candidates), None)


# Report the blocker of the account that passed the *most* gates — the most actionable
# one. A maxed-out-but-healthy account (quota) means "add accounts / raise the cap",
# which is more useful than reporting some other account that is merely not warmed yet.
# The two quota caps report separately (which one is full) but both outrank the rest.
_BLOCK_PRIORITY = ("quota_hour", "quota_day", "cooldown", "unhealthy", "not_ready")


def _account_block_reason(
    account_id: str,
    channel: str,
    channel_count: int,
    now: datetime,
    pool: _SelectionPool,
) -> str | None:
    """The first gate that makes one account ineligible, or ``None`` if it's eligible.

    Single source of the selection gate ladder — ``_is_eligible`` is just "no reason".
    """
    if _state.in_cooldown(account_id, now, channel):
        return "cooldown"
    account = pool.accounts.get(account_id)
    if account_id not in pool.ready_account_ids or account is None:
        return "not_ready"
    if not _is_healthy(account, channel_count, now, pool):
        return "unhealthy"
    return _quota_block_reason(account_id, pool.limits, pool.hourly_counts, pool.daily_counts)


def _is_eligible(
    account_id: str, channel: str, channel_count: int, now: datetime, pool: _SelectionPool
) -> bool:
    return _account_block_reason(account_id, channel, channel_count, now, pool) is None


def _selection_block_reason(
    account_ids: list[str], channel: str, channel_count: int, now: datetime, pool: _SelectionPool
) -> str:
    """Summarise why no account was eligible as the highest-priority binding blocker."""
    reasons = {
        reason
        for account_id in account_ids
        if (reason := _account_block_reason(account_id, channel, channel_count, now, pool))
    }
    return next((reason for reason in _BLOCK_PRIORITY if reason in reasons), "not_ready")


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


def _quota_block_reason(
    account_id: str,
    limits: NeurocommentSettings,
    hourly: dict[str, int],
    daily: dict[str, int],
) -> str | None:
    """Which cap the account has reached, or ``None`` while under both.

    ``quota_hour`` (per-account/hour) is reported before ``quota_day`` (per-channel/
    day) when both are full, so the log names the specific limit the operator hit.
    Quota counts in-flight claims AND delivered comments (status in claimed/posted),
    so a burst arriving inside one account's reply-delay window can't stack past the
    cap — each claim consumes quota the moment it is won.
    """
    if hourly.get(account_id, 0) >= limits.max_comments_per_hour:
        return "quota_hour"
    day_cap = limits.max_comments_per_channel_per_day
    if day_cap > 0 and daily.get(account_id, 0) >= day_cap:
        return "quota_day"
    return None


async def _account_quota_block_reason(
    account_id: str, channel: str, limits: NeurocommentSettings
) -> str | None:
    """Fresh single-account quota re-read (which cap, if any) under the lock before the claim.

    Reads only this account's fresh counts (not the whole fleet's grouped counts) — the
    re-check is per-account by nature, so the narrow single-account readers keep it
    O(1) rather than scanning every account's window. ``quota_hour`` outranks
    ``quota_day`` (same order as :func:`_quota_block_reason`).
    """
    now = datetime.now(UTC)
    hour_ago = (now - timedelta(hours=1)).isoformat()
    if await count_account_comments_since(account_id, hour_ago) >= limits.max_comments_per_hour:
        return "quota_hour"
    day_cap = limits.max_comments_per_channel_per_day
    if day_cap > 0:
        day_ago = (now - timedelta(days=1)).isoformat()
        if await count_account_channel_comments_since(account_id, channel, day_ago) >= day_cap:
            return "quota_day"
    return None


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
