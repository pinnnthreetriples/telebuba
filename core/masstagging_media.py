"""Story media of masstagging campaigns, in the shared content-addressed store.

Photos and videos alike: a story takes either. The store itself is
:mod:`core.content_store`; this module pins the directory and the suffix set.
"""

from __future__ import annotations

import asyncio

from core import content_store
from core.config import settings

MEDIA_SUFFIXES = content_store.IMAGE_SUFFIXES | content_store.VIDEO_SUFFIXES
VIDEO_SUFFIXES = content_store.VIDEO_SUFFIXES
MasstaggingMediaError = content_store.ContentStoreError


async def store_media(content: bytes, suffix: str) -> str:
    """Store a photo that decodes or a video with an MP4/MOV header; returns its id."""
    return await asyncio.to_thread(
        content_store.store, content, suffix, settings.masstagging.media_dir, MEDIA_SUFFIXES
    )


async def read_media(media_id: str) -> bytes:
    return await asyncio.to_thread(
        content_store.read, media_id, settings.masstagging.media_dir, MEDIA_SUFFIXES
    )
