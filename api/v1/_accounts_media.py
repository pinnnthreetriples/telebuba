"""Profile-media endpoints — photo / story / music mutations + the live snapshot.

Split out of ``accounts.py`` to keep that module under the file-size cap. These
routes are mounted onto the accounts router via ``include_router`` in
``accounts.py``; paths, operation ids, models, and error-envelope behavior are
unchanged.
"""

from __future__ import annotations

import base64
import binascii
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi import status as http_status

from api.v1._uploads import reject_oversized_upload
from core.config import settings
from schemas.profile_media import (
    AccountProfileMusicRemove,
    AccountProfileMusicUpload,
    AccountProfilePhotoRemove,
    AccountProfilePhotoSetMain,
    AccountProfilePhotoUpload,
    AccountProfileView,
    AccountStoryPin,
    AccountStoryRemove,
    AccountStoryUpload,
    MusicRemoveRequest,
    PhotoMainRequest,
    PhotoRemoveRequest,
    StoryMediaKind,
    StoryPinRequest,
    StoryPrivacyPreset,
    StoryRemoveRequest,
)
from schemas.telegram_actions import ActionResult
from services import accounts

# No tags here: this router is mounted onto the accounts ``router`` (which
# already carries ``tags=["accounts"]``), so tagging here would duplicate it.
media_router = APIRouter()


@media_router.post("/accounts/photo", response_model=ActionResult, operation_id="setAccountPhoto")
async def set_account_photo(
    account_id: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
) -> ActionResult:
    reject_oversized_upload(
        file,
        max_bytes=settings.profile_media.photo_max_bytes,
        detail="profile photo file is too large",
    )
    content = await file.read()
    upload = AccountProfilePhotoUpload(
        account_id=account_id,
        filename=file.filename or "photo.jpg",
        content=content,
    )
    try:
        return await accounts.set_account_profile_photo(upload)
    except accounts.AccountActionError:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _decode_ref(value: str) -> bytes:
    """Decode a base64 ``file_reference`` from the profile view, or 400."""
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="invalid file_reference",
        ) from exc


def _decode_id(value: str) -> int:
    """Parse an int64 identifier carried as a string in the profile view, or 400.

    The view sends photo_id / file_id / access_hash as decimal strings so the
    SPA doesn't round them past 2^53 (see schemas.profile_media._Int64Str).
    """
    try:
        return int(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="invalid id",
        ) from exc


@media_router.get(
    "/accounts/{account_id}/profile-snapshot",
    response_model=AccountProfileView,
    operation_id="getAccountProfileSnapshot",
)
async def get_account_profile_snapshot(
    account_id: str,
    refresh: Annotated[bool, Query()] = False,  # noqa: FBT002 - refresh flag
) -> AccountProfileView:
    """Live profile (name / bio / photos / stories / music) for the edit modal.

    ``refresh=true`` (the modal's «Обновить» button) bypasses the read cache and
    re-pulls from Telegram.
    """
    return await accounts.account_profile_view(account_id, force_refresh=refresh)


@media_router.post(
    "/accounts/{account_id}/story",
    response_model=ActionResult,
    operation_id="postAccountStory",
)
async def post_account_story(  # noqa: PLR0913 - one Form param per story field
    account_id: str,
    files: Annotated[list[UploadFile], File()],
    media_kind: Annotated[StoryMediaKind, Form()] = "image",
    caption: Annotated[str | None, Form()] = None,
    privacy_preset: Annotated[StoryPrivacyPreset, Form()] = "contacts",
    protect_content: Annotated[bool, Form()] = False,  # noqa: FBT002 - multipart form field
    collage_layout: Annotated[str | None, Form()] = None,
) -> ActionResult:
    # First file = image #1; any remaining files = the collage's images 2..N.
    # A single-file post is just a list of length 1 (the old single-photo path).
    if not files:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST, detail="no files uploaded"
        )
    if len(files) > settings.profile_media.story_collage_max_images:
        # Count-cap check BEFORE buffering every upload into RAM; the service
        # re-checks after decode (same stable locale-neutral code).
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="story_collage_too_many_images",
        )
    primary, *extras = files
    # Size-cap each part from the multipart size BEFORE buffering it into RAM;
    # the service re-checks after decode (same messages, defense-in-depth).
    primary_max_bytes = (
        settings.profile_media.story_image_max_bytes
        if media_kind == "image"
        else settings.profile_media.story_video_max_bytes
    )
    reject_oversized_upload(
        primary, max_bytes=primary_max_bytes, detail=f"story {media_kind} file is too large"
    )
    for extra in extras:
        reject_oversized_upload(
            extra,
            max_bytes=settings.profile_media.story_image_max_bytes,
            detail="story image file is too large",
        )
    upload = AccountStoryUpload(
        account_id=account_id,
        filename=primary.filename or "story",
        content=await primary.read(),
        media_kind=media_kind,
        caption=caption,
        privacy_preset=privacy_preset,
        protect_content=protect_content,
        extra_images=[await extra.read() for extra in extras],
        collage_layout=collage_layout,
    )
    try:
        return await accounts.post_account_story(upload)
    except accounts.AccountActionError:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@media_router.post(
    "/accounts/{account_id}/music",
    response_model=ActionResult,
    operation_id="addAccountMusic",
)
async def add_account_music(
    account_id: str,
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form()] = None,
    performer: Annotated[str | None, Form()] = None,
) -> ActionResult:
    reject_oversized_upload(
        file,
        max_bytes=settings.profile_media.music_max_bytes,
        detail="profile music file is too large",
    )
    content = await file.read()
    upload = AccountProfileMusicUpload(
        account_id=account_id,
        filename=file.filename or "track",
        content=content,
        title=title,
        performer=performer,
    )
    try:
        return await accounts.add_account_profile_music(upload)
    except accounts.AccountActionError:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@media_router.post(
    "/accounts/{account_id}/story/remove",
    response_model=ActionResult,
    operation_id="removeAccountStory",
)
async def remove_account_story(account_id: str, body: StoryRemoveRequest) -> ActionResult:
    try:
        return await accounts.remove_account_story(
            AccountStoryRemove(account_id=account_id, story_id=body.story_id),
        )
    except accounts.AccountActionError:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@media_router.post(
    "/accounts/{account_id}/story/pin",
    response_model=ActionResult,
    operation_id="setAccountStoryPinned",
)
async def set_account_story_pinned(account_id: str, body: StoryPinRequest) -> ActionResult:
    try:
        return await accounts.set_account_story_pinned(
            AccountStoryPin(account_id=account_id, story_id=body.story_id, pinned=body.pinned),
        )
    except accounts.AccountActionError:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@media_router.post(
    "/accounts/{account_id}/music/remove",
    response_model=ActionResult,
    operation_id="removeAccountMusic",
)
async def remove_account_music(account_id: str, body: MusicRemoveRequest) -> ActionResult:
    remove = AccountProfileMusicRemove(
        account_id=account_id,
        file_id=_decode_id(body.file_id),
        access_hash=_decode_id(body.access_hash),
        file_reference=_decode_ref(body.file_reference),
    )
    try:
        return await accounts.remove_account_profile_music(remove)
    except accounts.AccountActionError:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@media_router.post(
    "/accounts/{account_id}/photo/remove",
    response_model=ActionResult,
    operation_id="removeAccountPhoto",
)
async def remove_account_photo(account_id: str, body: PhotoRemoveRequest) -> ActionResult:
    remove = AccountProfilePhotoRemove(
        account_id=account_id,
        photo_id=_decode_id(body.photo_id),
        access_hash=_decode_id(body.access_hash),
        file_reference=_decode_ref(body.file_reference),
    )
    try:
        return await accounts.remove_account_profile_photo(remove)
    except accounts.AccountActionError:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@media_router.post(
    "/accounts/{account_id}/photo/main",
    response_model=ActionResult,
    operation_id="setAccountPhotoMain",
)
async def set_account_photo_main(account_id: str, body: PhotoMainRequest) -> ActionResult:
    set_main = AccountProfilePhotoSetMain(
        account_id=account_id,
        photo_id=_decode_id(body.photo_id),
        access_hash=_decode_id(body.access_hash),
        file_reference=_decode_ref(body.file_reference),
    )
    try:
        return await accounts.set_account_main_profile_photo(set_main)
    except accounts.AccountActionError:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
