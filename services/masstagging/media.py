"""Story media: admission policy in front of the core store.

A story takes a photo or a video, each within the size a profile story may be. The file
comes from the operator's disk, or from a story the account already has.
"""

from __future__ import annotations

from pathlib import PurePath
from typing import Final

from core.config import settings
from core.masstagging_media import (
    MEDIA_SUFFIXES,
    VIDEO_SUFFIXES,
    MasstaggingMediaError,
    store_media,
)
from core.telegram_client import TelegramAccountNotFoundError, TelegramReadError
from schemas.masstagging import MasstaggingMediaRead
from schemas.telegram_actions_media import DownloadStoryMedia
from schemas.telegram_profile_snapshot import TelegramStoryMedia
from services.masstagging import _seams
from services.masstagging._errors import (
    ACCOUNT_NOT_FOUND,
    MEDIA_INVALID,
    MEDIA_TOO_LARGE,
    STORY_UNAVAILABLE,
    MasstaggingInvalidError,
)

_MAX_NAME = 200
# What Telegram calls a story's file → the suffix the store keeps it under.
_MIME_SUFFIX: Final = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
}


def max_bytes(filename: str) -> int:
    """The ceiling for this file's kind; the route refuses a bigger body before reading."""
    limits = settings.profile_media
    if PurePath(filename).suffix.lower() in VIDEO_SUFFIXES:
        return limits.story_video_max_bytes
    return limits.story_image_max_bytes


async def _admit(filename: str, content: bytes) -> MasstaggingMediaRead:
    """Check the suffix and size, then store; refusals are 400 codes."""
    suffix = PurePath(filename).suffix.lower()
    if suffix not in MEDIA_SUFFIXES:
        raise MasstaggingInvalidError(MEDIA_INVALID)
    if len(content) > max_bytes(filename):
        raise MasstaggingInvalidError(MEDIA_TOO_LARGE)
    try:
        media_id = await store_media(content, suffix)
    except MasstaggingMediaError as exc:
        raise MasstaggingInvalidError(MEDIA_INVALID) from exc
    name = PurePath(filename).name[:_MAX_NAME] or media_id
    return MasstaggingMediaRead(media_id=media_id, name=name)


async def upload_media(filename: str, content: bytes) -> MasstaggingMediaRead:
    """Store a photo or a video for the campaign's stories; refusals are 400 codes."""
    return await _admit(filename, content)


async def import_story_media(account_id: str, story_id: int) -> MasstaggingMediaRead:
    """Take a story the account already has into the pool, as if it were uploaded.

    A story id means something only to the account that owns it, while the campaign's
    pool is shared by its whole roster, so the bytes are copied, never the id. Telegram
    answers an unknown or deleted id with nothing; a refused or flooded read is the same
    "unavailable" to the operator, who can only try again later.
    """
    try:
        answer = await _seams.execute_read(account_id, DownloadStoryMedia(story_id=story_id))
    except TelegramAccountNotFoundError as exc:
        raise MasstaggingInvalidError(ACCOUNT_NOT_FOUND) from exc
    except TelegramReadError as exc:
        raise MasstaggingInvalidError(STORY_UNAVAILABLE) from exc
    if not isinstance(answer, TelegramStoryMedia) or not answer.content:
        raise MasstaggingInvalidError(STORY_UNAVAILABLE)
    suffix = _MIME_SUFFIX.get(answer.mime_type.split(";")[0].strip().lower())
    if suffix is None:
        raise MasstaggingInvalidError(MEDIA_INVALID)
    return await _admit(f"story-{story_id}{suffix}", answer.content)
