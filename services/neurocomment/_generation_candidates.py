"""Generate, validate, and reserve candidate neurocomments."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    list_posted_comments_for_channel_since,
    load_warming_settings,
    touch_comment_claim,
)
from services.content import (
    is_acceptable,
    release_sent_text,
    similarity,
    strip_markdown_delimiters,
    try_reserve_sent,
)
from services.neurocomment import _seams
from services.neurocomment._llm import _build_request, _deepseek_generates, _gemini_reason, _Subject
from services.neurocomment._outcomes import _add_inflight, _inflight_texts, _provider_error

if TYPE_CHECKING:
    from schemas.neurocomment import NeurocommentCampaign
    from schemas.telegram_actions import NewPostEvent
    from schemas.telegram_actions_comments import PostCommentRecord
    from services.neurocomment._generate import _GenOutcome


async def generate_acceptable(
    campaign: NeurocommentCampaign,
    event: NewPostEvent,
    account_id: str,
    *,
    image_b64: str | None = None,
    target: PostCommentRecord | None = None,
) -> _GenOutcome:
    """Generate a reserved comment passing content and semantic-dedup gates."""
    from services.neurocomment._generate import (  # noqa: PLC0415 - compatibility facade
        _CLAIM_LOST_REASON,
        _GenOutcome,
        _log_regeneration,
    )

    nc = settings.neurocomment
    channel = event.channel
    recent = await recent_channel_comments(campaign.campaign_id, channel)
    if target is not None and nc.semantic_dedup_threshold > 0:
        # Reply mode: an answer that echoes the quoted comment is a ``duplicate`` of thread text.
        recent = [*recent, target.text]
    secret = await load_warming_settings()
    use_deepseek = _deepseek_generates(image_b64)
    generate = _seams.generate_text_deepseek if use_deepseek else _seams.generate_text
    reason: str | None = None
    error: str | None = None
    for attempt in range(nc.max_retries + 1):
        if not await touch_comment_claim(channel, event.post_id):
            return _GenOutcome(None, _CLAIM_LOST_REASON)
        await _log_regeneration(account_id, event, attempt, reason, error)
        generated = await generate(
            _build_request(
                campaign.prompt,
                _Subject(event.text, target),
                secret=secret,
                image_b64=image_b64,
                use_deepseek=use_deepseek,
            ),
        )
        if generated.status != "ok" or not generated.text:
            reason = _gemini_reason(generated)
            error = _provider_error(generated, use_deepseek=use_deepseek)
            continue
        candidate = strip_markdown_delimiters(generated.text).strip()
        error = None
        reason = await _candidate_rejection_reason(candidate, channel, recent)
        if reason is not None:
            continue
        if nc.semantic_dedup_threshold > 0:
            _add_inflight(channel, candidate, datetime.now(UTC))
        return _GenOutcome(candidate, None)
    return _GenOutcome(None, reason, error)


async def _candidate_rejection_reason(
    candidate: str,
    channel: str,
    recent: list[str],
) -> str | None:
    """Reserve one candidate, returning the first content/dedup rejection code."""
    nc = settings.neurocomment
    words = len(candidate.split())
    if words > nc.comment_max_words:
        return "too_long"
    if words < nc.comment_min_words:
        return "too_short"
    if not is_acceptable(candidate):
        return "not_acceptable"
    if not await try_reserve_sent(candidate):
        return "duplicate"
    inflight = (
        _inflight_texts(channel, datetime.now(UTC), nc.semantic_dedup_window_hours)
        if nc.semantic_dedup_threshold > 0
        else []
    )
    if any(
        similarity(candidate, previous) >= nc.semantic_dedup_threshold
        for previous in (*recent, *inflight)
    ):
        await release_sent_text(candidate)
        return "duplicate"
    return None


async def recent_channel_comments(campaign_id: str, channel: str) -> list[str]:
    """Return recent delivered text for this channel's semantic-dedup gate."""
    nc = settings.neurocomment
    if nc.semantic_dedup_threshold <= 0:
        return []
    since = (datetime.now(UTC) - timedelta(hours=nc.semantic_dedup_window_hours)).isoformat()
    posted = await list_posted_comments_for_channel_since(campaign_id, channel, since)
    return [comment.comment_text or "" for comment in posted.comments]


# Compatibility names used through ``_generate`` and ``engine`` test seams.
deepseek_generates = _deepseek_generates
build_request = _build_request
