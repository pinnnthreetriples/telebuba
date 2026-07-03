"""Neurocomment comment generation + outcome classification.

The back half of the on-post pipeline: generate a short on-prompt comment that
passes the word-count / content / exact-hash / semantic-dedup gates, pause a
human beat, post it, and classify the result (posted / cooldown / gate / failed)
with the matching state + audit writes. Split from ``engine`` for the file-size
budget; ``engine`` re-imports every name so ``handle_new_post`` keeps calling
them and ``services.neurocomment.engine.<name>`` still resolves.

Telegram / Gemini / randomness stay behind ``_seams``; the reply delay uses
``asyncio.sleep`` (tests patch ``asyncio.sleep`` via ``engine.asyncio``, the same
module object reached here).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    list_posted_comments_for_channel_since,
    load_warming_settings,
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
from services.neurocomment import _seams, _state
from services.neurocomment.settings_store import load_settings as load_neuro_settings

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

    # ``text`` is now reserved (the exact-hash claim). Any raise before ``_classify_post``
    # releases it — a delayed/cancelled attempt must not leave the hash reserved, or a
    # later regeneration of the same text is filtered as its own duplicate.
    try:
        limits = await load_neuro_settings()
        await asyncio.sleep(
            _seams.rng.uniform(limits.reply_delay_min_seconds, limits.reply_delay_max_seconds),
        )
        result = await _seams.execute(
            account_id,
            CommentOnPost(channel=event.channel, post_id=event.post_id, text=text),
        )
    except BaseException:
        await release_sent_text(text)
        raise
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
    # Comment generation always uses Gemini; read the operator's key from the DB
    # (falls back to .env) so a UI-set key takes effect without a restart.
    secret = await load_warming_settings()
    for _ in range(nc.max_retries + 1):
        request = _build_request(
            campaign.prompt, post_text, api_key=secret.gemini_api_key, model=secret.gemini_model
        )
        generated = await _seams.generate_text(request)
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


def _build_request(prompt: str, post_text: str, *, api_key: str, model: str) -> GeminiRequest:
    nc = settings.neurocomment
    instruction = (
        f"{prompt}\n\nReply to this post in at most {nc.comment_max_words} words, "
        f"as a natural reader comment. Post:\n{post_text}"
    )
    return GeminiRequest(
        api_key=api_key,
        prompt=instruction,
        model=model,
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
        # First comment confirms a solver click worked (no-op if no pending row). A
        # solved outcome resets the channel's challenge-failure window (#147) so
        # sporadic failures across many successes never accumulate to the trip count.
        if await resolve_pending_outcome(account_id, event.channel, "solved"):
            _state.reset_challenge_failures(event.channel)
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
