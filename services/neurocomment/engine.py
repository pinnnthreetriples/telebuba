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

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
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
    list_posted_comments_for_channel_since,
    list_spam_statuses,
    list_warming_states,
    mark_comment_failed,
    mark_comment_posted,
    resolve_pending_outcome,
    upsert_readiness,
)
from core.logging import log_event
from schemas.gemini import GeminiRequest
from schemas.telegram_actions import ActionResult, CommentOnPost, NewPostEvent
from services.content import (
    is_acceptable,
    release_sent_text,
    similarity,
    try_reserve_sent,
)
from services.neurocomment import _filters, _seams, _state
from services.trust import account_trust_score_from
from services.warming.pacing import evaluate_readiness

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.device_fingerprint import DeviceFingerprint
    from schemas.neurocomment import NeurocommentCampaign
    from schemas.spam_status import SpamStatusVerdict
    from schemas.warming import WarmingStateRecord

# Joined the group but writes are forbidden → a captcha/gate we detect and skip
# (mirrors onboarding's set). Flip readiness so the pair is no longer selected.
_GATE_ERRORS = frozenset(
    {"ChatGuestSendForbiddenError", "ChatWriteForbiddenError", "UserBannedInChannelError"},
)
# Rate-limit families that carry (or imply) a cooldown rather than a hard fail.
_COOLDOWN_STATUSES = frozenset(
    {"flood_wait", "slow_mode_wait", "premium_wait", "peer_flood"},
)


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

    await _generate_and_post(event, campaign, account_id)


class _SelectionPool(NamedTuple):
    """Bulk-loaded signals to score every candidate in one pass (no per-account I/O)."""

    accounts: dict[str, AccountRead]
    ready_account_ids: frozenset[str]  # accounts ready for THIS channel
    states: dict[str, WarmingStateRecord]
    spam: dict[str, SpamStatusVerdict]
    fingerprints: dict[str, DeviceFingerprint]
    hourly_counts: dict[str, int]
    daily_counts: dict[str, int]


async def _load_selection_pool(campaign_id: str, channel: str, now: datetime) -> _SelectionPool:
    """Bulk-read every selection signal once (mirrors ``services.neurocomment.board``)."""
    nc = settings.neurocomment
    accounts = {acc.account_id: acc for acc in (await list_accounts()).accounts}
    ready_account_ids = frozenset(
        r.account_id
        for r in (await list_campaign_readiness(campaign_id)).readiness
        if r.channel == channel and r.ready
    )
    states = {rec.account_id: rec for rec in await list_warming_states()}
    spam = await list_spam_statuses()
    fingerprints = await list_device_fingerprints()

    hour_ago = (now - timedelta(hours=1)).isoformat()
    hourly_rows = (await count_comments_per_account_since(hour_ago)).counts
    hourly = {c.account_id: c.count for c in hourly_rows}
    daily: dict[str, int] = {}
    if nc.max_comments_per_channel_per_day > 0:
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
    )


async def _select_account(campaign: NeurocommentCampaign, channel: str) -> str | None:
    """Pick one ready, healthy, under-quota, non-cooled account at random.

    Every signal is bulk-loaded once (mirroring ``services.neurocomment.board``), so
    selection scores N candidates from a handful of queries instead of ~7 per
    account — the cost stays flat as the fleet grows. Spam status is read from the
    cache and never re-probed here: probing @SpamBot per post is itself a ban
    signal, so warming/onboarding own spam freshness.
    """
    account_ids = [
        link.account_id for link in (await list_campaign_accounts(campaign.campaign_id)).links
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
    health = evaluate_readiness(account, channel_count, spam=spam, trust_score=trust)
    return health.ready


def _under_quota(account_id: str, pool: _SelectionPool) -> bool:
    # Quota counts in-flight claims AND delivered comments (status in claimed/posted),
    # so a burst arriving inside one account's reply-delay window can't stack past the
    # cap — each claim consumes quota the moment it is won.
    # ponytail: a sub-millisecond race still exists in the select->claim gap; a
    # per-account asyncio.Lock would close it fully if it ever bites.
    nc = settings.neurocomment
    day_cap = nc.max_comments_per_channel_per_day
    over_hour = pool.hourly_counts.get(account_id, 0) >= nc.max_comments_per_hour
    over_day = day_cap > 0 and pool.daily_counts.get(account_id, 0) >= day_cap
    return not (over_hour or over_day)


async def _generate_and_post(
    event: NewPostEvent,
    campaign: NeurocommentCampaign,
    account_id: str,
) -> None:
    """Generate + light-check a comment, pause, post, and classify the outcome."""
    text = await _generate_acceptable(campaign, event.channel, event.text)
    if text is None:
        await mark_comment_failed(event.channel, event.post_id)
        await log_event(
            "INFO",
            "neurocomment_generation_exhausted",
            account_id=account_id,
            extra={"channel": event.channel, "post_id": event.post_id},
        )
        return

    nc = settings.neurocomment
    await asyncio.sleep(_seams.rng.uniform(nc.reply_delay_min_seconds, nc.reply_delay_max_seconds))

    result = await _seams.execute(
        account_id,
        CommentOnPost(channel=event.channel, post_id=event.post_id, text=text),
    )
    await _classify_post(event, account_id, text, result)


async def _generate_acceptable(
    campaign: NeurocommentCampaign,
    channel: str,
    post_text: str,
) -> str | None:
    """Generate a comment passing word-count + filter + exact-hash + semantic dedup, or ``None``.

    Tries once plus ``max_retries`` regenerations. The exact-hash reservation is the
    atomic claim; the semantic check (token-set Jaccard vs the channel's recent posted
    comments) is layered after it as a cross-account near-duplicate guard. A
    reserved-but-rejected text is released so a later attempt isn't filtered as its own
    duplicate.
    """
    nc = settings.neurocomment
    recent = await _recent_channel_comments(campaign.campaign_id, channel)
    for _ in range(nc.max_retries + 1):
        generated = await _seams.generate_text(_build_request(campaign.prompt, post_text))
        if generated.status != "ok" or not generated.text:
            continue
        candidate = generated.text.strip()
        if len(candidate.split()) > nc.comment_max_words or not is_acceptable(candidate):
            continue
        if not await try_reserve_sent(candidate):
            continue
        # ponytail: `recent` is [] when semantic dedup is off (see _recent_channel_comments),
        # so this any() is the off-switch; don't also guard the threshold here.
        if any(similarity(candidate, prev) >= nc.semantic_dedup_threshold for prev in recent):
            await release_sent_text(candidate)
            continue
        return candidate
    return None


async def _recent_channel_comments(campaign_id: str, channel: str) -> list[str]:
    """The channel's recent posted comment texts for semantic dedup (empty when disabled)."""
    nc = settings.neurocomment
    if nc.semantic_dedup_threshold <= 0:
        return []
    since = (datetime.now(UTC) - timedelta(hours=nc.semantic_dedup_window_hours)).isoformat()
    posted = await list_posted_comments_for_channel_since(campaign_id, channel, since)
    return [c.comment_text or "" for c in posted.comments]


def _build_request(prompt: str, post_text: str) -> GeminiRequest:
    nc = settings.neurocomment
    instruction = (
        f"{prompt}\n\nReply to this post in at most {nc.comment_max_words} words, "
        f"as a natural reader comment. Post:\n{post_text}"
    )
    return GeminiRequest(
        api_key=settings.gemini.api_key,
        prompt=instruction,
        model=settings.gemini.model,
        temperature=settings.gemini.temperature,
        max_output_tokens=settings.gemini.max_output_tokens,
    )


async def _classify_post(
    event: NewPostEvent,
    account_id: str,
    text: str,
    result: ActionResult,
) -> None:
    if result.status == "ok":
        _state.clear_cooldown(account_id, event.channel)
        await mark_comment_posted(
            event.channel,
            event.post_id,
            comment_text=text,
            comment_msg_id=result.message_id,
        )
        # First comment confirms a solver click worked (no-op if no pending row).
        await resolve_pending_outcome(account_id, event.channel, "solved")
        await log_event(
            "INFO",
            "neurocomment_posted",
            account_id=account_id,
            extra={"channel": event.channel, "post_id": event.post_id},
        )
        return

    # Every non-ok path frees the claim's reserved text and marks the row failed.
    await release_sent_text(text)
    await mark_comment_failed(event.channel, event.post_id)

    if result.status in _COOLDOWN_STATUSES:
        # ponytail: MVP drops the lost post — it is NOT requeued for another
        # account. Post volume is low; a requeue is a follow-up if it bites.
        # slow-mode is per-chat → cool only this channel; flood/peer-flood/premium
        # are account-wide.
        scope = event.channel if result.status == "slow_mode_wait" else None
        _apply_cooldown(account_id, result.flood_wait_seconds, scope)
        event_name = "neurocomment_post_cooldown"
    elif result.error_type in _GATE_ERRORS:
        # Gate: stop selecting this pair until re-onboarded; the click did not work.
        await upsert_readiness(
            account_id,
            event.channel,
            joined=True,
            captcha_passed=False,
            ready=False,
        )
        if await resolve_pending_outcome(account_id, event.channel, "failed"):
            await _register_challenge_failure(event.channel)
        event_name = "neurocomment_post_gated"
    else:
        event_name = "neurocomment_post_failed"
    await log_event(
        "WARNING",
        event_name,
        account_id=account_id,
        extra={"channel": event.channel, "post_id": event.post_id, "status": result.status},
    )


def _apply_cooldown(account_id: str, flood_wait_seconds: int | None, channel: str | None) -> None:
    """Park ``(account, channel)``: flood duration, else the peer-flood config default."""
    seconds = flood_wait_seconds
    if seconds is None:
        # peer_flood (and any wait without a duration) → config cooldown.
        seconds = int(settings.neurocomment.peer_flood_cooldown_seconds)
    _state.set_cooldown(account_id, datetime.now(UTC) + timedelta(seconds=seconds), channel)


async def _register_challenge_failure(channel: str) -> None:
    """Count a solver click-failure on ``channel``; WARN once when it trips the back-off (#147)."""
    nc = settings.neurocomment
    cooldown = _state.register_challenge_failure(
        channel,
        datetime.now(UTC),
        min_failures=nc.channel_challenge_backoff_min_failures,
        base_seconds=nc.channel_challenge_backoff_base_seconds,
        max_seconds=nc.channel_challenge_backoff_max_seconds,
    )
    if cooldown is not None:
        await log_event(
            "WARNING",
            "neurocomment_challenge_backoff",
            extra={"channel": channel, "cooldown_seconds": cooldown},
        )
