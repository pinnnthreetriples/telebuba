"""Scheduled profile-post endpoints: upload media once, then schedule it per account.

Mounted onto the accounts router like the other ``_accounts_*`` siblings. Only
the media upload carries a file (and is on the large-upload allowlist in
``api/__init__.py``); scheduling itself is JSON, so a bulk run sends the file
over the wire once no matter how many accounts it targets.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi import status as http_status

from api.errors import SERVICE_ERRORS, error_responses
from api.v1._errors import service_errors_to_http
from api.v1._uploads import staged_upload
from core.config import settings
from schemas.scheduled_posts import (
    ScheduledMediaUploaded,
    ScheduledPostList,
    ScheduledPostRead,
    ScheduledPostReschedule,
    SchedulePhotoRequest,
    ScheduleStoryRequest,
)
from services import scheduled_posts

if TYPE_CHECKING:
    from collections.abc import Iterator

scheduled_router = APIRouter()

# A move or cancel can also find the post gone (404) or mid-publish (409).
_POST_ERRORS = error_responses(400, 404, 409, 503)


@contextmanager
def _post_errors() -> Iterator[None]:
    """Map a missing post to 404 and a post being published right now to 409."""
    try:
        yield
    except scheduled_posts.ScheduledPostNotFoundError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="scheduled_post_not_found",
        ) from exc
    except scheduled_posts.ScheduledPostConflictError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="scheduled_not_reschedulable",
        ) from exc


@scheduled_router.post(
    "/scheduled/media",
    response_model=ScheduledMediaUploaded,
    operation_id="uploadScheduledMedia",
    responses=SERVICE_ERRORS,
)
async def upload_scheduled_media(file: Annotated[UploadFile, File()]) -> ScheduledMediaUploaded:
    filename = file.filename or ""
    # Refused before a byte is staged; this also keeps the staged name's suffix to
    # the short whitelisted ones (a raw client suffix can break ``mkstemp``).
    if not scheduled_posts.accepts_filename(filename):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="scheduled_media_invalid",
        )
    # The ceiling here is the largest kind (video); the service applies the
    # per-kind cap once the suffix is known.
    async with staged_upload(
        file,
        max_bytes=settings.profile_media.story_video_max_bytes,
        detail="scheduled media file is too large",
        suffix=Path(filename).suffix.lower(),
    ) as path:
        with service_errors_to_http():
            return await scheduled_posts.store_media(path, filename)


@scheduled_router.post(
    "/accounts/{account_id}/scheduled/photo",
    response_model=ScheduledPostRead,
    operation_id="scheduleAccountPhoto",
    responses=SERVICE_ERRORS,
)
async def schedule_account_photo(
    account_id: str,
    body: SchedulePhotoRequest,
) -> ScheduledPostRead:
    with service_errors_to_http():
        return await scheduled_posts.schedule_photo(account_id, body)


@scheduled_router.post(
    "/accounts/{account_id}/scheduled/story",
    response_model=ScheduledPostRead,
    operation_id="scheduleAccountStory",
    responses=SERVICE_ERRORS,
)
async def schedule_account_story(
    account_id: str,
    body: ScheduleStoryRequest,
) -> ScheduledPostRead:
    with service_errors_to_http():
        return await scheduled_posts.schedule_story(account_id, body)


@scheduled_router.get(
    "/accounts/{account_id}/scheduled",
    response_model=ScheduledPostList,
    operation_id="listAccountScheduledPosts",
    responses=SERVICE_ERRORS,
)
async def list_account_scheduled_posts(account_id: str) -> ScheduledPostList:
    with service_errors_to_http():
        return await scheduled_posts.list_account_scheduled(account_id)


@scheduled_router.patch(
    "/accounts/{account_id}/scheduled/{post_id}",
    response_model=ScheduledPostRead,
    operation_id="rescheduleScheduledPost",
    responses=_POST_ERRORS,
)
async def reschedule_scheduled_post(
    account_id: str,
    post_id: str,
    body: ScheduledPostReschedule,
) -> ScheduledPostRead:
    with _post_errors(), service_errors_to_http():
        return await scheduled_posts.reschedule_scheduled_post(account_id, post_id, body)


@scheduled_router.delete(
    "/accounts/{account_id}/scheduled/{post_id}",
    response_model=ScheduledPostRead,
    operation_id="cancelScheduledPost",
    responses=_POST_ERRORS,
)
async def cancel_scheduled_post(account_id: str, post_id: str) -> ScheduledPostRead:
    with _post_errors(), service_errors_to_http():
        return await scheduled_posts.cancel_scheduled_post(account_id, post_id)
