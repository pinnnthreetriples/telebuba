"""Neurocomment comment generation, and the post attempt it hands to ``_outcomes``.

The back half of the on-post pipeline: generate a short on-prompt comment that
passes the word-count / content / exact-hash / semantic-dedup gates, pause a
human beat, and post it. Split from ``engine`` for the file-size budget; what the
attempt's answer COSTS (the outcome ladder and its state writes) is split off again
into ``_outcomes`` for the same reason and re-imported below, so ``engine``'s
re-exports and ``services.neurocomment.engine.<name>`` still resolve unchanged.

Telegram / Gemini / randomness stay behind ``_seams``; the reply delay uses
``asyncio.sleep`` (tests patch ``asyncio.sleep`` via ``engine.asyncio``, the same
module object reached here).
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
from core.db import (
    mark_comment_failed,
    mark_inbox_stage,
    release_claim,
    touch_comment_claim,
)
from core.logging import log_event
from schemas.neurocomment_pipeline import InboxStage, PipelineOutcome
from schemas.telegram_actions import CommentOnPost, NewPostEvent
from services.content import (
    release_sent_text,
)
from services.neurocomment import _seams
from services.neurocomment._outcomes import (  # noqa: F401 - _generate.<name> is the call-site path
    _COOLDOWN_STATUSES,
    _GATE_ERRORS,
    _INFLIGHT,
    _RATE_LIMITED_REASON,
    _add_inflight,
    _apply_cooldown,
    _classify_post,
    _inflight_texts,
    _remove_inflight,
)

if TYPE_CHECKING:
    from schemas.gemini import GeminiResult
    from schemas.neurocomment import NeurocommentCampaign, NeurocommentSettings


# Longest stretch the pipeline may go without telling the reclaim it is alive. Any value
# well under ``stale_claim_reclaim_seconds`` (900) works; 60s keeps the beat writes down to
# one a minute even on an operator delay measured in hours.
_CLAIM_BEAT_INTERVAL_SECONDS = 60.0

# Bound rather than inlined at the return below: a literal in a positional ``_GenOutcome``
# field is the one reason shape ``tests.test_logevent_i18n_parity`` cannot see, and this one
# reaches the operator through ``logEventReason`` — where a missing label renders as a blank,
# not as the code. A ``*_reason`` NAME is what puts it back under that guard.
_CLAIM_LOST_REASON = "claim_lost"


class _GenOutcome(NamedTuple):
    """A generated comment, or ``None`` with the last attempt's failure reason."""

    text: str | None
    reason: str | None  # set only when text is None (surfaced in the exhausted log)


async def _prepare_post_content(
    event: NewPostEvent,
    account_id: str,
) -> tuple[str | None, PipelineOutcome | None]:
    """Load a caption-less photo once, after the durable comment claim."""
    if event.media_kind != "photo" or event.text.strip():
        return None, None
    image = await _seams.download_post_image(
        account_id,
        event.channel,
        event.post_id,
        settings.neurocomment.vision_max_image_bytes,
    )
    if image.image_b64 is not None:
        return image.image_b64, None
    await mark_comment_failed(event.channel, event.post_id)
    await log_event(
        "INFO",
        "neurocomment_post_skipped",
        account_id=account_id,
        extra={
            "channel": event.channel,
            "post_id": event.post_id,
            "reason": f"media_{image.reason}",
        },
    )
    return None, PipelineOutcome.TERMINAL


async def _settle_generation_exhausted(
    event: NewPostEvent,
    account_id: str,
    outcome: _GenOutcome,
) -> PipelineOutcome:
    if outcome.reason == _RATE_LIMITED_REASON:
        await release_claim(event.channel, event.post_id)
        result = PipelineOutcome.RETRYABLE
    else:
        await mark_comment_failed(event.channel, event.post_id)
        result = PipelineOutcome.TERMINAL
    await log_event(
        "INFO",
        "neurocomment_generation_exhausted",
        account_id=account_id,
        extra={"channel": event.channel, "post_id": event.post_id, "reason": outcome.reason},
    )
    return result


async def _release_reserved_comment(event: NewPostEvent, text: str) -> None:
    _remove_inflight(event.channel, text)
    await release_sent_text(text)


async def _dispatch_reserved_comment(
    event: NewPostEvent,
    account_id: str,
    text: str,
    limits: NeurocommentSettings,
) -> PipelineOutcome:
    """Cross the dispatch boundary once, then settle its known or ambiguous result."""
    try:
        alive = await _sleep_beating(
            event,
            _seams.rng.uniform(limits.reply_delay_min_seconds, limits.reply_delay_max_seconds),
        )
        if not alive or not await touch_comment_claim(event.channel, event.post_id):
            await _release_reserved_comment(event, text)
            await log_event(
                "WARNING",
                "neurocomment_claim_lost_before_send",
                account_id=account_id,
                extra={"channel": event.channel, "post_id": event.post_id},
            )
            return PipelineOutcome.TERMINAL
        from services.neurocomment import _runtime  # noqa: PLC0415

        if not _runtime._worker_generation_is_current():  # noqa: SLF001
            await _release_reserved_comment(event, text)
            await release_claim(event.channel, event.post_id)
            return PipelineOutcome.RETRYABLE
        await mark_inbox_stage(event, InboxStage.DISPATCHING)
        try:
            result = await _seams.execute(
                account_id,
                CommentOnPost(channel=event.channel, post_id=event.post_id, text=text),
            )
            await mark_inbox_stage(event, InboxStage.DISPATCHED)
        except BaseException as exc:  # noqa: BLE001 - dispatch boundary includes cancellation
            await _settle_ambiguous_dispatch(
                event,
                account_id,
                text,
                event_code="neurocomment_dispatch_outcome_unknown",
                exc=exc,
            )
            return PipelineOutcome.AMBIGUOUS
    except BaseException:
        await _release_reserved_comment(event, text)
        raise
    try:
        await _classify_post(event, account_id, text, result)
    except Exception as exc:  # noqa: BLE001 - DB commit after dispatch is ambiguous
        # Telegram returned, but committing the verdict failed. Never reopen the claim:
        # a successful send may already be visible even if our DB write was lost.
        await _settle_ambiguous_dispatch(
            event,
            account_id,
            text,
            event_code="neurocomment_dispatch_commit_unknown",
            exc=exc,
        )
        return PipelineOutcome.AMBIGUOUS
    return PipelineOutcome.TERMINAL


async def _generate_and_post(
    event: NewPostEvent,
    campaign: NeurocommentCampaign,
    account_id: str,
    limits: NeurocommentSettings,
) -> PipelineOutcome:
    """Generate, reserve, human-delay and publish one durable comment attempt."""
    image_b64, terminal = await _prepare_post_content(event, account_id)
    if terminal is not None:
        return terminal
    generated = await _generate_acceptable(campaign, event, account_id, image_b64=image_b64)
    if generated.text is None:
        return await _settle_generation_exhausted(event, account_id, generated)
    return await _dispatch_reserved_comment(event, account_id, generated.text, limits)


async def _settle_ambiguous_dispatch(
    event: NewPostEvent,
    account_id: str,
    text: str,
    *,
    event_code: str,
    exc: BaseException,
) -> None:
    """Best-effort cleanup that can never reopen a crossed dispatch boundary."""
    # Every await below is deliberately isolated. Once DISPATCHING is durable, even a
    # broken SQLite/log sink/content-reservation cleanup must still return AMBIGUOUS to
    # the inbox; letting any cleanup exception escape would make engine release the claim
    # as though Telegram had never been called.
    with suppress(Exception, asyncio.CancelledError):
        await mark_comment_failed(event.channel, event.post_id)
    with suppress(Exception, asyncio.CancelledError):
        await log_event(
            "ERROR",
            event_code,
            account_id=account_id,
            extra={
                "channel": event.channel,
                "post_id": event.post_id,
                "error_type": type(exc).__name__,
            },
        )
    with suppress(Exception):
        _remove_inflight(event.channel, text)
    with suppress(Exception, asyncio.CancelledError):
        await release_sent_text(text)


async def _sleep_beating(event: NewPostEvent, seconds: float) -> bool:
    """Wait ``seconds``, beating the claim between slices; ``False`` if the claim went.

    The reply delay is the one long stretch the operator sets directly, and it is spent
    INSIDE a claim — so a single beat placed after it covers nothing at all. Sliced instead,
    a delay of any length keeps its claim alive, which is why the write schema needs no
    arbitrary upper bound on it — and why the one it briefly had would have locked the
    Settings form: the read model is unbounded and every save resends the whole object, so
    one already-stored value above the cap 422s every unrelated edit with no field flagged.
    """
    remaining = seconds
    while remaining > 0:
        if not await touch_comment_claim(event.channel, event.post_id):
            return False
        chunk = min(remaining, _CLAIM_BEAT_INTERVAL_SECONDS)
        await asyncio.sleep(chunk)
        remaining -= chunk
    return True


def _gemini_reason(result: GeminiResult) -> str:
    """Classify a non-usable Gemini result for the exhausted-generation log."""
    if result.status == "rate_limited":
        return _RATE_LIMITED_REASON
    if result.status == "ok":  # 200 but no text — safety block / empty candidates
        return "gemini_empty"
    return "gemini_error"


async def _log_regeneration(
    account_id: str,
    event: NewPostEvent,
    attempt: int,
    reason: str | None,
) -> None:
    """Say that this post is being written again, and what was wrong with the last try.

    Only a REPEAT round earns a line, which is why round zero is filtered HERE rather than
    at the call site: this is the rule, and the ladder above it is already at its branch
    budget. A first-try comment is the normal case on every post, so logging that too
    would double the feed's volume and say nothing. Reaching a round with ``attempt`` set
    means the previous one failed a check — a usable candidate returns, and a lost claim
    returns before this — so ``reason`` is always set here, and filtering on it as well
    keeps that invariant honest instead of logging a blank.

    ``reason`` carries that failing check's own code and NOT the position in the budget,
    which is what it used to carry. A bare "2/2" beside the label is exactly how the three
    DAILY rules render theirs — re-join, channel pause, join request — so two regenerations
    of one post, a second apart, read as a channel that had burned two days of its retry
    budget. The rounds are still countable: they are consecutive lines on the same
    ``post_id``, and the raw numbers ride along in ``attempt`` / ``max_retries``.
    """
    if not attempt or reason is None:
        return
    await log_event(
        "INFO",
        "neurocomment_generation_retry",
        account_id=account_id,
        extra={
            "channel": event.channel,
            "post_id": event.post_id,
            "reason": reason,
            "attempt": attempt,
            "max_retries": settings.neurocomment.max_retries,
        },
    )


from services.neurocomment import _generation_candidates as _candidates  # noqa: E402

_build_request = _candidates.build_request
_generate_acceptable = _candidates.generate_acceptable
_recent_channel_comments = _candidates.recent_channel_comments
