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
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    claim_comment,
    count_account_channel_comments_since,
    count_account_comments_since,
    fetch_account,
    fetch_active_campaign_for_channel,
    fetch_readiness,
    list_campaign_accounts,
    list_campaign_channels,
    list_posted_comments_since,
    mark_comment_failed,
    mark_comment_posted,
    upsert_readiness,
)
from core.logging import log_event
from schemas.gemini import GeminiRequest
from schemas.telegram_actions import ActionResult, CommentOnPost, NewPostEvent
from services.content import (
    has_link,
    is_acceptable,
    release_sent_text,
    similarity,
    try_reserve_sent,
)
from services.neurocomment import _seams, _state
from services.trust import account_trust_score
from services.warming.pacing import evaluate_readiness

if TYPE_CHECKING:
    from schemas.neurocomment import NeurocommentCampaign

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

    skip = _filter_reason(event)
    if skip is not None:
        await log_event(
            "INFO",
            "neurocomment_post_skipped",
            extra={"channel": event.channel, "post_id": event.post_id, "reason": skip},
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


def _filter_reason(event: NewPostEvent) -> str | None:
    """Return why we skip this post, or ``None`` to proceed."""
    # getattr defense: if a NewPostEvent without is_forward ever reaches here
    # (e.g. a bad merge of the listener schema), degrade to "don't filter forwards"
    # rather than AttributeError-killing every post through the catch-all.
    if getattr(event, "is_forward", False):
        return "forward"
    text = event.text.strip()
    if event.has_media and not text:
        return "media_no_caption"
    if not text and not event.has_media:
        return "empty"
    if _is_link_only(event.text):
        return "link_only"
    return None


def _is_link_only(text: str) -> bool:
    """True when the text is essentially just a link / ad (few real word chars).

    Drops the link tokens themselves, then counts the remaining word characters —
    a post that is only a URL leaves almost nothing behind.
    """
    if not has_link(text):
        return False
    without_links = " ".join(token for token in text.split() if not has_link(token))
    stripped = "".join(ch for ch in without_links if ch.isalnum())
    return len(stripped) <= settings.neurocomment.link_only_max_word_chars


async def _select_account(campaign: NeurocommentCampaign, channel: str) -> str | None:
    """Pick one ready, healthy, under-quota, non-cooled account at random."""
    links = (await list_campaign_accounts(campaign.campaign_id)).links
    channel_count = max(1, len((await list_campaign_channels(campaign.campaign_id)).links))
    now = datetime.now(UTC)
    candidates = [
        link.account_id
        for link in links
        if await _is_eligible(link.account_id, channel, channel_count, now)
    ]
    if not candidates:
        return None
    return _seams.rng.choice(candidates)


async def _is_eligible(account_id: str, channel: str, channel_count: int, now: datetime) -> bool:
    if _state.in_cooldown(account_id, now, channel):
        return False
    readiness = await fetch_readiness(account_id, channel)
    if readiness is None or not readiness.ready:
        return False
    if not await _is_healthy(account_id, channel_count):
        return False
    return await _under_quota(account_id, channel, now)


async def _is_healthy(account_id: str, channel_count: int) -> bool:
    """Reuse the warming readiness gate as the account health check."""
    account = await fetch_account(account_id)
    if account is None:
        return False
    spam = await _seams.refresh_spam_status(account_id, force=False)
    trust = await account_trust_score(account_id)
    health = evaluate_readiness(account, channel_count, spam=spam, trust_score=trust)
    return health.ready


async def _under_quota(account_id: str, channel: str, now: datetime) -> bool:
    # Quota counts in-flight claims AND delivered comments (status in claimed/posted),
    # so a burst arriving inside one account's reply-delay window can't stack past the
    # cap — each claim consumes quota the moment it is won.
    # ponytail: a sub-millisecond race still exists in the select->claim gap; a
    # per-account asyncio.Lock would close it fully if it ever bites.
    nc = settings.neurocomment
    hour_ago = (now - timedelta(hours=1)).isoformat()
    if await count_account_comments_since(account_id, hour_ago) >= nc.max_comments_per_hour:
        return False
    if nc.max_comments_per_channel_per_day > 0:
        day_ago = (now - timedelta(days=1)).isoformat()
        used = await count_account_channel_comments_since(account_id, channel, day_ago)
        if used >= nc.max_comments_per_channel_per_day:
            return False
    return True


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
    posted = await list_posted_comments_since(campaign_id, since)
    return [c.comment_text or "" for c in posted.comments if c.channel == channel]


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
        # Lazy captcha/gate: stop selecting this (account, channel) until re-onboarded.
        await upsert_readiness(
            account_id,
            event.channel,
            joined=True,
            captcha_passed=False,
            ready=False,
        )
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
