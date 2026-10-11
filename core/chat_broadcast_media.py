"""Photos attached to chat-broadcast messages, in the shared content-addressed store.

The store itself is :mod:`core.content_store`; this module pins the broadcast's
directory and its photo-only suffix set. Photos are profile-photo sized, so they travel
as bytes.
"""

from __future__ import annotations

import asyncio

from core import content_store
from core.config import settings

PHOTO_SUFFIXES = content_store.IMAGE_SUFFIXES
BroadcastMediaError = content_store.ContentStoreError


async def store_photo(content: bytes, suffix: str) -> str:
    """Store a decodable image; returns its media id. ``suffix`` is pre-validated."""
    return await asyncio.to_thread(
        content_store.store, content, suffix, settings.chat_broadcast.media_dir, PHOTO_SUFFIXES
    )


async def read_photo(media_id: str) -> bytes:
    return await asyncio.to_thread(
        content_store.read, media_id, settings.chat_broadcast.media_dir, PHOTO_SUFFIXES
    )
