"""Media and Premium extras runners — video / voice consumption and the emoji status.

Video and voice spend a second budget beside the daily attempts: a per-cycle byte
allowance (``ctx.media_bytes_left``) that core enforces as the download cap. It is
debited BEFORE dispatch for the same reason attempts are booked before dispatch — a
download cancelled mid-flight may already have moved the bytes. The emoji status is
Premium-only and never probes by writing: ``needs={"premium"}`` gates it on the
account's known Premium flag, so a non-Premium (or unknown) account never sends it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from schemas.telegram_actions_warming import WarmConsumeMedia, WarmEmojiStatus
from services.warming import _seams
from services.warming._extras_ctx import MEDIA_MIN_BYTES, _recent_posts, _write
from services.warming._steps import _human_pause

if TYPE_CHECKING:
    from typing import Literal

    from schemas.telegram_actions import ActionResult
    from services.warming._extras_ctx import _ExtraContext

# Upper bound of the status draw; core wraps it modulo the real default-status count,
# so this only shapes the spread, never the validity.
_EMOJI_STATUSES_MAX = 64


async def _consume(ctx: _ExtraContext, kind: Literal["video", "voice"]) -> ActionResult | None:
    # Eligibility was judged before the draw; an earlier media extra in the same cycle may
    # have spent the budget below the action floor since.
    if ctx.media_bytes_left < MEDIA_MIN_BYTES:
        return None
    warm = settings.warming
    channel, ids = _recent_posts(ctx)
    max_bytes = min(warm.extras_media_bytes_per_item, ctx.media_bytes_left)
    # Debit first (fail-closed): a cancelled download may already have moved the bytes.
    ctx.media_bytes_left -= max_bytes
    action = WarmConsumeMedia(channel=channel, message_ids=ids, kind=kind, max_bytes=max_bytes)
    result = await _write(ctx, action)
    if result.status == "ok" and result.message_id is not None:
        # A viewer lingers on what just played before the next tap (skips played nothing).
        await _human_pause(*warm.extras_media_pause_seconds)
    return result


async def video(ctx: _ExtraContext) -> ActionResult | None:
    return await _consume(ctx, "video")


async def voice(ctx: _ExtraContext) -> ActionResult | None:
    return await _consume(ctx, "voice")


async def emoji_status(ctx: _ExtraContext) -> ActionResult:
    warm = settings.warming
    action = WarmEmojiStatus(
        status_index=_seams.rng.randrange(_EMOJI_STATUSES_MAX),
        until_hours=_seams.rng.uniform(*warm.extras_emoji_until_hours),
        clear=_seams.rng.random() < warm.extras_emoji_clear_probability,
    )
    return await _write(ctx, action)
