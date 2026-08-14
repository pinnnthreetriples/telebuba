"""Generate, reserve, and durably dispatch one neurocomment."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
from core.db import (
    mark_comment_failed,
    release_claim,
    set_comment_dispatch_stage,
    touch_comment_claim,
)
from core.logging import log_event
from schemas.neurocomment_pipeline import PipelineOutcome
from schemas.telegram_actions import CommentOnPost, NewPostEvent
from services.content import release_sent_text
from services.neurocomment import _seams
from services.neurocomment._llm import (  # noqa: F401 - compatibility facade
    _build_request,
    _deepseek_generates,
    _gemini_reason,
    _post_clause,
    _Subject,
)
from services.neurocomment._outcomes import (  # noqa: F401 - compatibility facade
    _COOLDOWN_STATUSES,
    _GATE_ERRORS,
    _INFLIGHT,
    _RATE_LIMITED_REASON,
    _add_inflight,
    _apply_cooldown,
    _classify_post,
    _inflight_texts,
    _provider_error,
    _remove_inflight,
)

if TYPE_CHECKING:
    from schemas.neurocomment import NeurocommentCampaign, NeurocommentSettings
    from schemas.telegram_actions_comments import PostCommentRecord

# Stdlib sink for evidence that must survive a log-store fault — see
# ``core.proxy_check._failed_result`` for the same route.
logger = logging.getLogger(__name__)

_CLAIM_BEAT_INTERVAL_SECONDS = 60.0
_CLAIM_LOST_REASON = "claim_lost"


class _GenOutcome(NamedTuple):
    """A generated comment, or the last rejection and provider detail."""

    text: str | None
    reason: str | None
    error: str | None = None


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
    extra: dict[str, object] = {
        "channel": event.channel,
        "post_id": event.post_id,
        "reason": outcome.reason,
    }
    if outcome.error:
        extra["error_type"] = outcome.error
    await log_event(
        "INFO",
        "neurocomment_generation_exhausted",
        account_id=account_id,
        extra=extra,
    )
    return result


async def _release_reserved_comment(event: NewPostEvent, text: str) -> None:
    _remove_inflight(event.channel, text)
    await release_sent_text(text)


async def _settle_revoked_dispatch(
    event: NewPostEvent,
    account_id: str,
    text: str,
    exc: _seams.NeurocommentLeaseRevokedError,
) -> PipelineOutcome:
    if isinstance(exc, _seams.NeurocommentLeaseLostAfterDispatchError):
        await _settle_ambiguous_dispatch(
            event,
            account_id,
            text,
            event_code="neurocomment_dispatch_outcome_unknown",
            exc=exc,
        )
        return PipelineOutcome.AMBIGUOUS
    await _release_reserved_comment(event, text)
    if isinstance(exc, _seams.NeurocommentAccountDeletedError):
        # No account row means no later attempt can ever succeed, so the surviving
        # ``failed`` row is the right answer. Every other refusal here — warming,
        # mid-handoff, a revoked generation — ends on its own and issued no Telegram
        # request, so the claim is deleted and the post goes back on the retry ladder.
        await mark_comment_failed(event.channel, event.post_id)
        return PipelineOutcome.TERMINAL
    await release_claim(event.channel, event.post_id)
    return PipelineOutcome.RETRYABLE


async def _dispatch_reserved_comment(  # noqa: PLR0911 - explicit durable outcomes
    event: NewPostEvent,
    account_id: str,
    text: str,
    limits: NeurocommentSettings,
    *,
    target: PostCommentRecord | None = None,
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
        try:
            result = await _seams.execute_comment(
                account_id,
                CommentOnPost(
                    channel=event.channel,
                    post_id=event.post_id,
                    text=text,
                    reply_to=target.message_id if target is not None else None,
                ),
                lambda: set_comment_dispatch_stage(event, "dispatching"),
            )
            # ``False`` means the row was reclaimed or removed while Telegram was in
            # flight. The boundary has still been crossed and ``result`` is authoritative:
            # let the normal outcome path persist a returned message id (or emit the
            # row-missing diagnostic) rather than downgrading a known delivery to an
            # ambiguous one. A storage exception remains ambiguous in the handler below.
            await set_comment_dispatch_stage(event, "dispatched")
        except _seams.NeurocommentPreDispatchCancelledError:
            await _release_reserved_comment(event, text)
            await release_claim(event.channel, event.post_id)
            raise
        except _seams.NeurocommentPreDispatchError:
            await _release_reserved_comment(event, text)
            await release_claim(event.channel, event.post_id)
            return PipelineOutcome.RETRYABLE
        except _seams.NeurocommentLeaseRevokedError as exc:
            return await _settle_revoked_dispatch(event, account_id, text, exc)
        except BaseException as exc:  # noqa: BLE001 - cancellation is ambiguous here
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
    except Exception as exc:  # noqa: BLE001 - a post-send DB fault is ambiguous
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
    *,
    target: PostCommentRecord | None = None,
) -> PipelineOutcome:
    """Generate, reserve, human-delay and publish one durable comment attempt."""
    image_b64, terminal = await _prepare_post_content(event, account_id)
    if terminal is not None:
        return terminal
    # Telemetry is not a precondition for the paid generation/send path. In
    # particular, a logging outage must not turn a later dispatch fault from
    # AMBIGUOUS into a seemingly pre-send RETRYABLE outcome.
    with suppress(Exception):
        await log_event(
            "INFO",
            "neurocomment_generation_started",
            account_id=account_id,
            extra={"channel": event.channel, "post_id": event.post_id},
        )
    generated = await _generate_acceptable(
        campaign,
        event,
        account_id,
        image_b64=image_b64,
        target=target,
    )
    if generated.text is None:
        return await _settle_generation_exhausted(event, account_id, generated)
    return await _dispatch_reserved_comment(
        event,
        account_id,
        generated.text,
        limits,
        target=target,
    )


async def _settle_ambiguous_dispatch(
    event: NewPostEvent,
    account_id: str,
    text: str,
    *,
    event_code: str,
    exc: BaseException,
) -> None:
    """Best-effort cleanup that can never reopen a crossed dispatch boundary."""
    # An ambiguous post is the one ending nothing else can recover from: it may already
    # carry a live comment, so no retry is allowed and the record below is all the
    # operator ever gets. Everything here is suppressed — including the event write —
    # so the stdlib sink goes first and keeps the evidence even when the log store is
    # the very thing that faulted.
    logger.error(
        "%s for %s/%s after %s",
        event_code,
        event.channel,
        event.post_id,
        type(exc).__name__,
    )
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
    """Wait in bounded slices, returning false once the durable claim is gone."""
    remaining = seconds
    while remaining > 0:
        if not await touch_comment_claim(event.channel, event.post_id):
            return False
        chunk = min(remaining, _CLAIM_BEAT_INTERVAL_SECONDS)
        await asyncio.sleep(chunk)
        remaining -= chunk
    return True


async def _log_regeneration(
    account_id: str,
    event: NewPostEvent,
    attempt: int,
    reason: str | None,
    error: str | None = None,
) -> None:
    """Report only repeat rounds, including the failing provider when known."""
    if not attempt or reason is None:
        return
    extra: dict[str, object] = {
        "channel": event.channel,
        "post_id": event.post_id,
        "reason": reason,
        "attempt": attempt,
        "max_retries": settings.neurocomment.max_retries,
    }
    if error:
        extra["error_type"] = error
    await log_event(
        "INFO",
        "neurocomment_generation_retry",
        account_id=account_id,
        extra=extra,
    )


from services.neurocomment import _generation_candidates as _candidates  # noqa: E402

_generate_acceptable = _candidates.generate_acceptable
_recent_channel_comments = _candidates.recent_channel_comments
