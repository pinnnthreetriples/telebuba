"""Photos for chain messages: admission policy in front of the core store."""

from __future__ import annotations

from pathlib import PurePath

from core.chat_broadcast_media import PHOTO_SUFFIXES, BroadcastMediaError, store_photo
from core.config import settings
from schemas.chat_broadcast import ChatBroadcastMediaRead
from services.chat_broadcast._errors import (
    MEDIA_INVALID,
    MEDIA_TOO_LARGE,
    ChatBroadcastInvalidError,
)

_MAX_NAME = 200


async def upload_photo(filename: str, content: bytes) -> ChatBroadcastMediaRead:
    """Store a photo the size and type a profile photo may be; refusals are 400 codes."""
    suffix = PurePath(filename).suffix.lower()
    if suffix not in PHOTO_SUFFIXES:
        raise ChatBroadcastInvalidError(MEDIA_INVALID)
    if len(content) > settings.profile_media.photo_max_bytes:
        raise ChatBroadcastInvalidError(MEDIA_TOO_LARGE)
    try:
        media_id = await store_photo(content, suffix)
    except BroadcastMediaError as exc:
        raise ChatBroadcastInvalidError(MEDIA_INVALID) from exc
    name = PurePath(filename).name[:_MAX_NAME] or media_id
    return ChatBroadcastMediaRead(media_id=media_id, name=name)
