"""Bounded thumbnail downloads shared by the photo / story read dispatchers.

One semaphore per read batch caps how many ``download_media`` calls run at
once — an unbounded ``asyncio.gather`` over up to 100 thumbs hammered the DC
in parallel. The first ``FloodWaitError`` trips a shared breaker: exactly one
structured log event, and every not-yet-started download in the batch degrades
to ``None`` (no thumbnail) instead of piling more requests onto a rate-limited
connection. The dialog still opens; thumbs simply render as placeholders.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from telethon import errors

from core.config import settings
from core.logging import log_event

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

__all__ = ["download_thumb_bounded", "thumb_limiter"]


def thumb_limiter() -> tuple[asyncio.Semaphore, asyncio.Event]:
    """Per-batch concurrency gate: (semaphore, flood-breaker flag)."""
    return (
        asyncio.Semaphore(settings.profile_media.thumb_concurrency),
        asyncio.Event(),
    )


async def download_thumb_bounded(
    semaphore: asyncio.Semaphore,
    flood_stop: asyncio.Event,
    kind: str,
    download: Callable[[], Awaitable[bytes | None]],
) -> bytes | None:
    """Run one thumb download under the batch semaphore; trip on FloodWait.

    ``FloodWaitError`` sets the shared ``flood_stop`` flag (logging once) so
    sibling downloads in the same batch skip instead of hammering; the item
    degrades to ``None`` — never raises, the modal must still open.
    """
    async with semaphore:
        if flood_stop.is_set():
            return None
        try:
            return await download()
        except errors.FloodWaitError as exc:
            if not flood_stop.is_set():
                flood_stop.set()
                await log_event(
                    "WARNING",
                    "telegram_thumb_download_flood_wait",
                    extra={"kind": kind, "seconds": exc.seconds},
                )
            return None
