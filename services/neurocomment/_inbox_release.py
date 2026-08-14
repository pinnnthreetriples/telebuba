"""The endings a post can reach without being commented, and who hears about them.

Split out of ``_inbox_runtime`` for the file-size cap, like ``_sweep`` and
``_lifecycle`` before it. The three endings here share one property that is the whole
reason they are written down: the repository is their only witness. It moves the row to
``done`` and returns a code, and until this module read that code an operator could not
tell a post that exhausted its retries, or one abandoned mid-send, from one the listener
never received.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from core.db import release_post, requeue_processing_posts
from core.logging import log_event
from schemas.neurocomment_pipeline import ReleaseOutcome

if TYPE_CHECKING:
    from schemas.logs import LogLevel
    from schemas.telegram_actions import NewPostEvent

# Both endings take the post out of the pipeline for good, and neither is recoverable:
# a spent budget has nothing left to spend, and a row past ``pre_send`` may already
# carry a live comment, so re-sending it is the one thing that must not happen.
_FINAL_EVENTS: dict[ReleaseOutcome, tuple[LogLevel, str]] = {
    ReleaseOutcome.EXHAUSTED: ("WARNING", "neurocomment_inbox_retry_exhausted"),
    ReleaseOutcome.AMBIGUOUS: ("ERROR", "neurocomment_inbox_dispatch_ambiguous"),
}


async def retry(event: NewPostEvent) -> None:
    """Retry only proven pre-send failures with exponential bounded backoff."""
    nc = settings.neurocomment
    # Attempts were incremented by the claim. The repository owns the final cap; using
    # the configured maximum here gives an upper bound without another hot-path read.
    outcome = await release_post(
        event,
        nc.post_inbox_max_attempts,
        nc.post_inbox_retry_base_seconds,
        nc.post_inbox_retry_max_seconds,
    )
    reportable = _FINAL_EVENTS.get(outcome)
    if reportable is not None:
        level, code = reportable
        await log_event(level, code, extra={"channel": event.channel, "post_id": event.post_id})


async def recover_processing() -> None:
    """Requeue orphaned rows and report the ones a restart refuses to resend.

    Settling a row found at ``dispatching``/``dispatched`` as ambiguous is right — its
    comment may be live on Telegram. But it leaves the pipeline with no comment and, in
    silence, no word about it either.
    """
    recovery = await requeue_processing_posts()
    if recovery.ambiguous:
        await log_event(
            "ERROR",
            "neurocomment_inbox_recovery_ambiguous",
            extra={"posts": recovery.ambiguous, "requeued": recovery.requeued},
        )
