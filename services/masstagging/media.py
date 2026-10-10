"""Story media: admission policy in front of the core store.

A story takes a photo or a video, each within the size a profile story may be.
"""

from __future__ import annotations

from pathlib import PurePath

from core.config import settings
from core.masstagging_media import (
    MEDIA_SUFFIXES,
    VIDEO_SUFFIXES,
    MasstaggingMediaError,
    store_media,
)
from schemas.masstagging import MasstaggingMediaRead
from services.masstagging._errors import MEDIA_INVALID, MEDIA_TOO_LARGE, MasstaggingInvalidError

_MAX_NAME = 200


def max_bytes(filename: str) -> int:
    """The ceiling for this file's kind; the route refuses a bigger body before reading."""
    limits = settings.profile_media
    if PurePath(filename).suffix.lower() in VIDEO_SUFFIXES:
        return limits.story_video_max_bytes
    return limits.story_image_max_bytes


async def upload_media(filename: str, content: bytes) -> MasstaggingMediaRead:
    """Store a photo or a video for the campaign's stories; refusals are 400 codes."""
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
