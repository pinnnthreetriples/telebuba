"""Pull a channel post's photo in-memory, for the caption-less-post vision path.

A direct gateway function rather than a ``TelegramReadAction`` — the pattern
``check_spam_status`` and ``refresh_account_avatar`` already use. Nothing an operator
asks for, sees in the activity log, or batches with other reads: it is one internal
fetch on the way to a comment, so it does not belong in the operator-facing action union.

Never raises, like the spam probe. The whole point is to let the neurocomment engine
comment on a caption-less photo instead of throwing the post away, so every failure
mode answers "no image, and here is why" and the caller falls back to the skip it was
already doing.

Also home to ``download_photo_b64``, the capped still-only pull that the guardian-bot
captcha solver (``_read_challenge``) shares: both take bytes a third party chose,
straight into this process's RAM, on their way to a vision model — so both want one
ceiling, one size choice, and one deadline rather than two copies of the idiom.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import TYPE_CHECKING

from telethon.tl.types import (
    MessageMediaPhoto,
    PhotoCachedSize,
    PhotoSize,
    PhotoSizeProgressive,
    PhotoStrippedSize,
)

from core.telegram_client._pool import get_client
from schemas.telegram_actions import PostImageResult

if TYPE_CHECKING:
    from telethon import TelegramClient

# Stdlib sink for full third-party text — see ``core.proxy_check._failed_result``.
logger = logging.getLogger(__name__)

# Wall-clock ceiling on ONE photo fetch, connection included. Telethon has no deadline of
# its own here: it pulls a file in sequential 128 KB parts (``get_appropriated_part_size``)
# and each ``upload.getFile`` may legitimately burn ``request_retries`` (3) x ``timeout``
# (20s) plus retry delays — ~64s — before it gives up. A proxy that is slow rather than
# dead therefore had no upper bound at all, and past ``stale_claim_reclaim_seconds`` (900s)
# the sweep marks the row ``failed`` under a still-live worker, whose delivered comment
# ``mark_comment_posted`` then silently drops on its terminal-status guard.
# 30s is ~4s per part at the shipped 1 MB cap (8 parts) — slower than any proxy worth
# commenting through — and keeps the longest legitimate in-flight stretch (~273s) under a
# third of that cutoff. Not a setting: nobody tunes "how long may one picture take", they
# turn the vision path off with ``vision_max_image_bytes``.
PHOTO_FETCH_TIMEOUT_SECONDS = 30.0


async def download_post_image(
    account_id: str,
    channel: str,
    post_id: int,
    max_bytes: int,
) -> PostImageResult:
    """Fetch post ``post_id``'s photo as base64 on ``account_id``'s pooled client.

    Runs on the account that is about to comment: it is already a member here, so the
    post reads without resolving anything new, and the fetch shares the connection the
    comment itself will use. Any fault (flood wait, RPC, dead proxy, pool failure, or
    the fetch outstaying ``PHOTO_FETCH_TIMEOUT_SECONDS``) collapses to ``unavailable``
    — it says nothing about the post, but it still leaves the model with nothing to look
    at, which is the only thing the caller can act on.
    """
    try:
        # One deadline over the whole thing, not over the byte pull alone: acquiring a
        # pooled client dials the proxy, which is exactly where a bad proxy stalls.
        async with asyncio.timeout(PHOTO_FETCH_TIMEOUT_SECONDS):
            client = await get_client(account_id)
            # ``ids=<int>`` returns the single message (or None once it is deleted /
            # invisible), unlike the list form — same idiom as the deletion sweep's
            # alive check.
            message = await client.get_messages(channel, ids=post_id)
            if not isinstance(getattr(message, "media", None), MessageMediaPhoto):
                return PostImageResult(reason="unavailable")
            return await download_photo_b64(client, message, max_bytes)
    except Exception as exc:  # noqa: BLE001 - one picture must never kill the post pipeline
        # Full text to the stdlib sink only: the caller copies the outcome into a log
        # row that ``GET /logs`` serves back verbatim, and a pooled-client failure
        # stringifies WITH the proxy endpoint (non-negotiable #12, same call as _spam).
        logger.warning("post image download failed for %s %s", channel, post_id, exc_info=exc)
        return PostImageResult(reason="unavailable")


def _still_byte_count(size: object) -> int | None:
    """Bytes one photo size weighs, or ``None`` when it is not a downloadable still.

    Mirrors ``telethon.utils._photo_size_byte_count`` (private, hence copied): each size
    class states its weight differently, and getting that wrong is the difference between
    a gate and a guess. ``VideoSize`` is deliberately absent — see ``download_photo_b64``.
    """
    if isinstance(size, PhotoSizeProgressive):
        return max(size.sizes, default=0)
    if isinstance(size, PhotoSize):
        return size.size
    if isinstance(size, (PhotoCachedSize, PhotoStrippedSize)):
        return len(size.bytes)
    # PhotoSizeEmpty, PhotoPathSize (an SVG outline, which Telethon drops too), anything
    # a future layer adds: nothing we can fetch and nothing we can weigh.
    return None


async def download_photo_b64(
    client: TelegramClient,
    message: object,
    max_bytes: int,
) -> PostImageResult:
    """Base64 the biggest STILL size of ``message``'s photo that fits ``max_bytes``.

    The gate names the size it is about to pull (``thumb=<type>``) instead of measuring
    ``message.file.size`` and letting Telethon choose for itself. Those are two different
    lists by construction: ``File.size`` maps only ``photo.sizes``, while
    ``TelegramClient._download_photo`` picks from ``photo.sizes + photo.video_sizes`` and
    ``sort_thumbs`` ranks a ``VideoSize`` above EVERY still. So a caption-less "animated"
    photo used to clear the cap on its tens-of-KB still and then pull the whole MP4 into
    RAM — the exact cost this pre-check exists to avoid — and, if the video happened to
    fit, hand it to the model labelled ``image/jpeg``, which it can only reject. Naming
    the type makes gate and pull agree, and keeps the bytes an actual still image.
    (A type can only resolve to our own size: stills sort ahead of video sizes, and
    Telethon takes the first match.)

    Taking the biggest still that FITS, rather than the biggest and then refusing, is what
    lets the ceiling be low enough to matter: a 3 MB 2560px original falls back to its
    800px sibling — all a vision model resolves anyway — instead of costing the post its
    comment. ``too_large`` is left for a photo with no size small enough to be worth it.
    """
    sizes = getattr(getattr(getattr(message, "media", None), "photo", None), "sizes", None) or []
    weighed = [
        (count, str(getattr(size, "type", "")))
        for size in sizes
        if (count := _still_byte_count(size)) is not None
    ]
    if not weighed:
        return PostImageResult(reason="unavailable")
    fitting = [entry for entry in weighed if entry[0] <= max_bytes]
    if not fitting:
        return PostImageResult(reason="too_large")
    _, thumb = max(fitting)
    # Telethon idioms: the `bytes` type pulls in-memory (no temp file) and `thumb=<type>`
    # names the size to fetch. Its stubs admit neither; the bound alias keeps that one
    # ignore on one line rather than smeared over three argument lines.
    download = client.download_media
    data = await download(message, file=bytes, thumb=thumb)  # ty: ignore[invalid-argument-type]
    if not isinstance(data, (bytes, bytearray)):
        return PostImageResult(reason="unavailable")
    if len(data) > max_bytes:
        # Belt behind the gate, for a size that under-reported itself: the bytes are
        # already here, but they never reach the base64 that would multiply them by 4/3.
        return PostImageResult(reason="too_large")
    return PostImageResult(image_b64=base64.b64encode(bytes(data)).decode("ascii"))
