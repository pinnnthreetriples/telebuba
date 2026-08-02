"""Pull a channel post's photo in-memory, for the caption-less-post vision path.

A direct gateway function rather than a ``TelegramReadAction`` — the pattern
``check_spam_status`` and ``refresh_account_avatar`` already use. Nothing an operator
asks for, sees in the activity log, or batches with other reads: it is one internal
fetch on the way to a comment, so it does not belong in the operator-facing action union.

Never raises, like the spam probe. The whole point is to let the neurocomment engine
comment on a caption-less photo instead of throwing the post away, so every failure
mode answers "no image, and here is why" and the caller falls back to the skip it was
already doing.
"""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING

from telethon.tl.types import MessageMediaPhoto

from core.telegram_client._pool import get_client
from schemas.telegram_actions import PostImageResult

if TYPE_CHECKING:
    from telethon import TelegramClient

# Stdlib sink for full third-party text — see ``core.proxy_check._failed_result``.
logger = logging.getLogger(__name__)


async def download_post_image(
    account_id: str,
    channel: str,
    post_id: int,
    max_bytes: int,
) -> PostImageResult:
    """Fetch post ``post_id``'s photo as base64 on ``account_id``'s pooled client.

    Runs on the account that is about to comment: it is already a member here, so the
    post reads without resolving anything new, and the fetch shares the connection the
    comment itself will use. Any fault (flood wait, RPC, dead proxy, pool failure)
    collapses to ``unavailable`` — it says nothing about the post, but it still leaves
    the model with nothing to look at, which is the only thing the caller can act on.
    """
    try:
        client = await get_client(account_id)
        return await _fetch_photo(client, channel, post_id, max_bytes)
    except Exception as exc:  # noqa: BLE001 - one picture must never kill the post pipeline
        # Full text to the stdlib sink only: the caller copies the outcome into a log
        # row that ``GET /logs`` serves back verbatim, and a pooled-client failure
        # stringifies WITH the proxy endpoint (non-negotiable #12, same call as _spam).
        logger.warning("post image download failed for %s %s", channel, post_id, exc_info=exc)
        return PostImageResult(reason="unavailable")


async def _fetch_photo(
    client: TelegramClient,
    channel: str,
    post_id: int,
    max_bytes: int,
) -> PostImageResult:
    """Read the post, gate it on size, and base64 the bytes.

    The size gate is checked against ``message.file.size`` BEFORE downloading, so an
    oversized photo costs one message read instead of the bytes; the post-download
    re-check is the belt for a message whose metadata is absent or lying.
    """
    # ``ids=<int>`` returns the single message (or None once it is deleted / invisible),
    # unlike the list form — same idiom as the deletion sweep's alive check.
    message = await client.get_messages(channel, ids=post_id)
    if not isinstance(getattr(message, "media", None), MessageMediaPhoto):
        return PostImageResult(reason="unavailable")
    size = getattr(getattr(message, "file", None), "size", None)
    if isinstance(size, int) and size > max_bytes:
        return PostImageResult(reason="too_large")
    # Telethon idiom: passing the `bytes` type downloads in-memory (its stub types
    # `file` too narrowly to admit it).
    data = await client.download_media(message, file=bytes)  # ty: ignore[invalid-argument-type]
    if not isinstance(data, (bytes, bytearray)):
        return PostImageResult(reason="unavailable")
    if len(data) > max_bytes:
        return PostImageResult(reason="too_large")
    return PostImageResult(image_b64=base64.b64encode(bytes(data)).decode("ascii"))
