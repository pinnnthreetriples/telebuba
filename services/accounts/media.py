"""Profile photo / story / music upload flows.

``execute`` is imported at module scope so tests can monkeypatch
``services.accounts.media.execute``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from core.telegram_client import execute
from schemas.telegram_actions import (
    AddProfileMusic,
    PostStory,
    RemoveProfileMusic,
    RemoveProfilePhoto,
    SetProfilePhoto,
)
from services.accounts._uploads import (
    _PROFILE_MUSIC_SUFFIXES,
    _PROFILE_PHOTO_SUFFIXES,
    _STORY_IMAGE_SUFFIXES,
    _STORY_VIDEO_SUFFIXES,
    _validate_upload,
)
from services.accounts.profile_read import invalidate_account_profile_cache

if TYPE_CHECKING:
    from schemas.profile_media import (
        AccountProfileMusicRemove,
        AccountProfileMusicUpload,
        AccountProfilePhotoRemove,
        AccountProfilePhotoUpload,
        AccountStoryUpload,
    )
    from schemas.telegram_actions import ActionResult

__all__ = [
    "add_account_profile_music",
    "post_account_story",
    "remove_account_profile_music",
    "remove_account_profile_photo",
    "set_account_profile_photo",
]


async def set_account_profile_photo(data: AccountProfilePhotoUpload) -> ActionResult:
    _validate_upload(
        filename=data.filename,
        content=data.content,
        max_bytes=settings.profile_media.photo_max_bytes,
        allowed_suffixes=_PROFILE_PHOTO_SUFFIXES,
        label="profile photo",
    )
    result = await execute(
        data.account_id,
        SetProfilePhoto(filename=data.filename, content=data.content),
    )
    if result.status != "ok":
        msg = result.error_message or result.status
        raise ValueError(msg)
    invalidate_account_profile_cache(data.account_id)
    await log_event(
        "INFO",
        "account_profile_photo_updated",
        account_id=data.account_id,
        extra={"filename": data.filename},
    )
    return result


async def post_account_story(data: AccountStoryUpload) -> ActionResult:
    max_bytes = (
        settings.profile_media.story_image_max_bytes
        if data.media_kind == "image"
        else settings.profile_media.story_video_max_bytes
    )
    allowed_suffixes = (
        _STORY_IMAGE_SUFFIXES if data.media_kind == "image" else _STORY_VIDEO_SUFFIXES
    )
    _validate_upload(
        filename=data.filename,
        content=data.content,
        max_bytes=max_bytes,
        allowed_suffixes=allowed_suffixes,
        label=f"story {data.media_kind}",
    )
    result = await execute(
        data.account_id,
        PostStory(
            filename=data.filename,
            content=data.content,
            media_kind=data.media_kind,
            caption=data.caption,
            privacy_preset=data.privacy_preset,
            period_seconds=data.period_seconds,
            protect_content=data.protect_content,
        ),
    )
    if result.status != "ok":
        msg = result.error_message or result.status
        raise ValueError(msg)
    invalidate_account_profile_cache(data.account_id)
    await log_event(
        "INFO",
        "account_story_posted",
        account_id=data.account_id,
        extra={
            "filename": data.filename,
            "media_kind": data.media_kind,
            "privacy_preset": data.privacy_preset,
        },
    )
    return result


async def add_account_profile_music(data: AccountProfileMusicUpload) -> ActionResult:
    _validate_upload(
        filename=data.filename,
        content=data.content,
        max_bytes=settings.profile_media.music_max_bytes,
        allowed_suffixes=_PROFILE_MUSIC_SUFFIXES,
        label="profile music",
    )
    result = await execute(
        data.account_id,
        AddProfileMusic(
            filename=data.filename,
            content=data.content,
            title=data.title,
            performer=data.performer,
        ),
    )
    if result.status != "ok":
        msg = result.error_message or result.status
        raise ValueError(msg)
    invalidate_account_profile_cache(data.account_id)
    await log_event(
        "INFO",
        "account_profile_music_added",
        account_id=data.account_id,
        extra={"filename": data.filename, "has_title": data.title is not None},
    )
    return result


async def remove_account_profile_music(data: AccountProfileMusicRemove) -> ActionResult:
    result = await execute(
        data.account_id,
        RemoveProfileMusic(
            file_id=data.file_id,
            access_hash=data.access_hash,
            file_reference=data.file_reference,
        ),
    )
    if result.status != "ok":
        msg = result.error_message or result.status
        raise ValueError(msg)
    invalidate_account_profile_cache(data.account_id)
    await log_event(
        "INFO",
        "account_profile_music_removed",
        account_id=data.account_id,
        extra={"file_id": data.file_id},
    )
    return result


async def remove_account_profile_photo(data: AccountProfilePhotoRemove) -> ActionResult:
    result = await execute(
        data.account_id,
        RemoveProfilePhoto(
            photo_id=data.photo_id,
            access_hash=data.access_hash,
            file_reference=data.file_reference,
        ),
    )
    if result.status != "ok":
        msg = result.error_message or result.status
        raise ValueError(msg)
    invalidate_account_profile_cache(data.account_id)
    await log_event(
        "INFO",
        "account_profile_photo_removed",
        account_id=data.account_id,
        extra={"photo_id": data.photo_id},
    )
    return result
