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
    RemoveStory,
    SetMainProfilePhoto,
    SetProfilePhoto,
    ToggleStoryPinned,
)
from services.accounts._result import AccountActionError, raise_for_result
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
        AccountProfilePhotoSetMain,
        AccountProfilePhotoUpload,
        AccountStoryPin,
        AccountStoryRemove,
        AccountStoryUpload,
    )
    from schemas.telegram_actions import ActionResult

__all__ = [
    "add_account_profile_music",
    "post_account_story",
    "remove_account_profile_music",
    "remove_account_profile_photo",
    "remove_account_story",
    "set_account_main_profile_photo",
    "set_account_profile_photo",
    "set_account_story_pinned",
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
    raise_for_result(result)
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
    if data.extra_images:
        # Collage: only images can carry extra photos, and the whole set must fit
        # the count window. Codes are locale-neutral (non-negotiable #12).
        if data.media_kind != "image":
            code = "story_collage_requires_image"
            raise AccountActionError(code)
        for extra in data.extra_images:
            _validate_upload(
                filename=data.filename,
                content=extra,
                max_bytes=settings.profile_media.story_image_max_bytes,
                allowed_suffixes=_STORY_IMAGE_SUFFIXES,
                label="story image",
            )
        if 1 + len(data.extra_images) > settings.profile_media.story_collage_max_images:
            code = "story_collage_too_many_images"
            raise AccountActionError(code)
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
            extra_images=data.extra_images,
            collage_layout=data.collage_layout,
        ),
    )
    raise_for_result(result)
    invalidate_account_profile_cache(data.account_id)
    await log_event(
        "INFO",
        "account_story_posted",
        account_id=data.account_id,
        extra={
            "filename": data.filename,
            "media_kind": data.media_kind,
            "privacy_preset": data.privacy_preset,
            "image_count": 1 + len(data.extra_images),
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
    raise_for_result(result)
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
    raise_for_result(result)
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
    raise_for_result(result)
    invalidate_account_profile_cache(data.account_id)
    await log_event(
        "INFO",
        "account_profile_photo_removed",
        account_id=data.account_id,
        extra={"photo_id": data.photo_id},
    )
    return result


async def set_account_main_profile_photo(data: AccountProfilePhotoSetMain) -> ActionResult:
    result = await execute(
        data.account_id,
        SetMainProfilePhoto(
            photo_id=data.photo_id,
            access_hash=data.access_hash,
            file_reference=data.file_reference,
        ),
    )
    # Invalidate BEFORE raising on failure: a failed promote can still have
    # touched server state, and a kept-stale snapshot makes the operator
    # re-click photo ids that no longer exist (log-proven 2026-07-13 18:11:39).
    invalidate_account_profile_cache(data.account_id)
    raise_for_result(result)
    await log_event(
        "INFO",
        "account_profile_photo_set_main",
        account_id=data.account_id,
        extra={"photo_id": data.photo_id},
    )
    return result


async def remove_account_story(data: AccountStoryRemove) -> ActionResult:
    result = await execute(data.account_id, RemoveStory(story_id=data.story_id))
    raise_for_result(result)
    invalidate_account_profile_cache(data.account_id)
    await log_event(
        "INFO",
        "account_story_removed",
        account_id=data.account_id,
        extra={"story_id": data.story_id},
    )
    return result


async def set_account_story_pinned(data: AccountStoryPin) -> ActionResult:
    result = await execute(
        data.account_id,
        ToggleStoryPinned(story_id=data.story_id, pinned=data.pinned),
    )
    raise_for_result(result)
    invalidate_account_profile_cache(data.account_id)
    await log_event(
        "INFO",
        "account_story_pinned",
        account_id=data.account_id,
        extra={"story_id": data.story_id, "pinned": data.pinned},
    )
    return result
