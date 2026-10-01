"""Scheduling policy: accept media, create / list / move / cancel scheduled posts.

Everything that can be refused is refused HERE, when the operator schedules,
not hours later when the post is due: file type and size, decodability, the
publish-time window, collage shape, and the per-account / store capacity.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from core import scheduled_media
from core.config import settings
from core.logging import log_event
from core.repositories import scheduled_posts as repo
from core.telegram_client import is_known_collage_layout
from schemas.profile_media import ProfileImage
from schemas.scheduled_posts import (
    ScheduledMediaUploaded,
    ScheduledPost,
    ScheduledPostList,
    ScheduledPostRead,
    ScheduledStoryOptions,
)
from services.accounts._result import AccountNotFoundError
from services.accounts.lifecycle import require_account
from services.scheduled_posts import _runtime

if TYPE_CHECKING:
    from schemas.scheduled_posts import (
        ScheduledPostRefusalCode,
        ScheduledPostReschedule,
        SchedulePhotoRequest,
        ScheduleStoryRequest,
    )

# Settled posts stay in the list this long, so the operator sees what happened.
_LIST_FINAL_WINDOW_SECONDS = 86_400
_MEDIA_INVALID: ScheduledPostRefusalCode = "scheduled_media_invalid"
_STORE_FULL: ScheduledPostRefusalCode = "scheduled_store_full"
_OUT_OF_RANGE: ScheduledPostRefusalCode = "scheduled_run_at_out_of_range"
_PENDING_LIMIT: ScheduledPostRefusalCode = "scheduled_pending_limit"
_MEDIA_MISSING: ScheduledPostRefusalCode = "scheduled_media_missing"
_KIND_MISMATCH: ScheduledPostRefusalCode = "scheduled_media_kind_mismatch"
_TOO_MANY_IMAGES: ScheduledPostRefusalCode = "scheduled_too_many_images"
_BAD_LAYOUT: ScheduledPostRefusalCode = "scheduled_collage_layout_invalid"


class ScheduledPostRefusedError(ValueError):
    """A scheduling refusal; ``str(exc)`` is the stable code the SPA translates."""

    def __init__(self, code: ScheduledPostRefusalCode) -> None:
        self.code = code
        super().__init__(code)


class ScheduledPostNotFoundError(LookupError):
    pass


class ScheduledPostConflictError(Exception):
    """The post is being published right now (or already finished)."""


def _max_bytes_for(suffix: str) -> int:
    if suffix in scheduled_media.VIDEO_SUFFIXES:
        return settings.profile_media.story_video_max_bytes
    return max(settings.profile_media.photo_max_bytes, settings.profile_media.story_image_max_bytes)


def accepts_filename(filename: str) -> bool:
    """Whether a file of this name could be stored at all — checked before staging."""
    suffix = Path(filename).suffix.lower()
    return suffix in scheduled_media.IMAGE_SUFFIXES | scheduled_media.VIDEO_SUFFIXES


async def store_media(source: Path, filename: str) -> ScheduledMediaUploaded:
    """Validate an uploaded file and keep it until the posts that use it are done."""
    if not accepts_filename(filename):
        raise ScheduledPostRefusedError(_MEDIA_INVALID)
    suffix = Path(filename).suffix.lower()
    is_video = suffix in scheduled_media.VIDEO_SUFFIXES
    size = (await asyncio.to_thread(source.stat)).st_size
    if size == 0 or size > _max_bytes_for(suffix):
        raise ScheduledPostRefusedError(_MEDIA_INVALID)
    if not is_video:
        try:
            await scheduled_media.verify_image(source)
        except scheduled_media.MediaStoreError as exc:
            raise ScheduledPostRefusedError(_MEDIA_INVALID) from exc
    async with scheduled_media.store_lock():
        stored = await scheduled_media.ingest(source, suffix)
        # Measured after the fact, so a re-upload of stored content costs nothing.
        if (
            stored.created
            and await scheduled_media.total_bytes() > settings.scheduled_posts.max_store_bytes
        ):
            await scheduled_media.discard(stored.name)
            raise ScheduledPostRefusedError(_STORE_FULL)
    return ScheduledMediaUploaded(
        media_id=stored.name,
        media_kind="video" if is_video else "image",
        size_bytes=stored.size,
    )


def _run_at_unix(run_at: datetime) -> int:
    value = int(run_at.timestamp())
    now = int(time.time())
    earliest = now + settings.scheduled_posts.min_lead_seconds
    latest = now + settings.scheduled_posts.max_lead_days * 86_400
    if not earliest <= value <= latest:
        raise ScheduledPostRefusedError(_OUT_OF_RANGE)
    return value


async def _insert(post: ScheduledPost) -> ScheduledPostRead:
    """Insert under the store lock.

    No sweep can then drop a file between the check and the row, and two
    concurrent requests cannot both squeeze under the per-account cap.
    """
    async with scheduled_media.store_lock():
        # Again under the lock: a concurrent retry of the same key may have taken
        # the account's last slot between the first lookup and here.
        if post.batch_id is not None and post.client_key is not None:
            twin = await repo.fetch_keyed(post.batch_id, post.account_id, post.client_key)
            if twin is not None:
                return to_read(twin)
        if await repo.count_open_for_account(post.account_id) >= (
            settings.scheduled_posts.max_pending_per_account
        ):
            raise ScheduledPostRefusedError(_PENDING_LIMIT)
        for name in post.media_names:
            if not await scheduled_media.touch_existing(name):
                raise ScheduledPostRefusedError(_MEDIA_MISSING)
        inserted = await repo.insert_post(post)
    if inserted is None:
        raise AccountNotFoundError(post.account_id)
    row, created = inserted
    if created:
        _runtime.wake()
        await log_event(
            "INFO",
            "account_scheduled_post_created",
            account_id=post.account_id,
            extra={"kind": post.kind, "media_count": len(post.media_names)},
        )
    return to_read(row)


def _new_post(
    account_id: str,
    request: SchedulePhotoRequest | ScheduleStoryRequest,
    media_names: list[str],
    story: ScheduledStoryOptions | None,
) -> ScheduledPost:
    run_at = _run_at_unix(request.run_at)
    created_at, updated_unix = repo.new_post_timestamps()
    return ScheduledPost(
        post_id=uuid.uuid4().hex,
        account_id=account_id,
        kind="photo" if story is None else "story",
        state="pending",
        stage="queued",
        scheduled_for_unix=run_at,
        next_attempt_unix=run_at,
        media_names=media_names,
        filename=request.filename,
        story=story,
        batch_id=request.batch_id,
        client_key=request.client_key,
        created_at=created_at,
        updated_unix=updated_unix,
    )


async def _already_created(
    account_id: str,
    request: SchedulePhotoRequest | ScheduleStoryRequest,
) -> ScheduledPostRead | None:
    """The post a keyed retry already made, if any.

    Looked up BEFORE any check that could now refuse it: its time may have
    passed, and its account may be at the cap by now. A retry that carries a
    different time (the operator moved the row after a lost answer) moves that
    post instead of leaving it at the old time beside a second copy.
    """
    if request.batch_id is None or request.client_key is None:
        return None
    row = await repo.fetch_keyed(request.batch_id, account_id, request.client_key)
    if row is None:
        return None
    wanted = int(request.run_at.timestamp())
    if row.scheduled_for_unix != wanted and row.state in {"pending", "missed", "failed"}:
        await repo.reschedule_post(row.post_id, account_id, _run_at_unix(request.run_at))
        _runtime.wake()
        row = await repo.fetch_post(row.post_id) or row
    return to_read(row)


async def schedule_photo(account_id: str, request: SchedulePhotoRequest) -> ScheduledPostRead:
    await require_account(account_id)
    if (existing := await _already_created(account_id, request)) is not None:
        return existing
    if scheduled_media.is_video(request.media_id):
        raise ScheduledPostRefusedError(_KIND_MISMATCH)
    # Uploads are capped for the larger image kind; a profile photo has its own cap.
    size = await scheduled_media.size_of(request.media_id)
    if size is not None and size > settings.profile_media.photo_max_bytes:
        raise ScheduledPostRefusedError(_MEDIA_INVALID)
    return await _insert(_new_post(account_id, request, [request.media_id], None))


async def schedule_story(account_id: str, request: ScheduleStoryRequest) -> ScheduledPostRead:
    await require_account(account_id)
    if (existing := await _already_created(account_id, request)) is not None:
        return existing
    primary, *extras = request.media_ids
    is_video = scheduled_media.is_video(primary)
    # A collage stitches still images only; a video story is a single file.
    if (is_video and extras) or any(scheduled_media.is_video(name) for name in extras):
        raise ScheduledPostRefusedError(_KIND_MISMATCH)
    if len(request.media_ids) > settings.profile_media.story_collage_max_images:
        raise ScheduledPostRefusedError(_TOO_MANY_IMAGES)
    if extras and not is_known_collage_layout(len(request.media_ids), request.collage_layout):
        raise ScheduledPostRefusedError(_BAD_LAYOUT)
    if not is_video:
        # Uploads are capped for the larger image kind; a story image has its own cap.
        for name in request.media_ids:
            size = await scheduled_media.size_of(name)
            if size is not None and size > settings.profile_media.story_image_max_bytes:
                raise ScheduledPostRefusedError(_MEDIA_INVALID)
    story = ScheduledStoryOptions(
        media_kind="video" if is_video else "image",
        caption=request.caption,
        privacy_preset=request.privacy_preset,
        protect_content=request.protect_content,
        collage_layout=request.collage_layout if extras else None,
        period_seconds=request.period_seconds,
    )
    return await _insert(_new_post(account_id, request, list(request.media_ids), story))


def _iso(unix: int) -> datetime:
    return datetime.fromtimestamp(unix, tz=UTC)


def _thumb_url(post: ScheduledPost) -> str | None:
    if post.state in {"done", "cancelled"} or scheduled_media.is_video(post.media_names[0]):
        return None
    return f"/api/{settings.api.version}/accounts/{post.account_id}/scheduled/{post.post_id}/thumb"


def to_read(post: ScheduledPost) -> ScheduledPostRead:
    return ScheduledPostRead(
        post_id=post.post_id,
        kind=post.kind,
        state=post.state,
        run_at=_iso(post.scheduled_for_unix),
        next_attempt_at=_iso(post.next_attempt_unix),
        finished_at=None if post.finished_unix is None else _iso(post.finished_unix),
        attempts=post.attempts,
        error_code=post.error_code,
        filename=post.filename,
        media_kind="video" if scheduled_media.is_video(post.media_names[0]) else "image",
        media_count=len(post.media_names),
        caption=None if post.story is None else post.story.caption,
        privacy_preset=None if post.story is None else post.story.privacy_preset,
        story_id=post.story_id,
        thumb_url=_thumb_url(post),
    )


async def list_account_scheduled(account_id: str) -> ScheduledPostList:
    await require_account(account_id)
    since = int(time.time()) - _LIST_FINAL_WINDOW_SECONDS
    rows = await repo.list_account_posts(account_id, since)
    return ScheduledPostList(items=[to_read(row) for row in rows], server_now=datetime.now(UTC))


async def _require_post(account_id: str, post_id: str) -> ScheduledPost:
    post = await repo.fetch_post(post_id)
    if post is None or post.account_id != account_id:
        raise ScheduledPostNotFoundError(post_id)
    return post


async def reschedule_scheduled_post(
    account_id: str,
    post_id: str,
    request: ScheduledPostReschedule,
) -> ScheduledPostRead:
    await _require_post(account_id, post_id)
    run_at = _run_at_unix(request.run_at)
    async with scheduled_media.store_lock():
        # A missed / failed post keeps its files, but a restored backup may not.
        post = await _require_post(account_id, post_id)
        for name in post.media_names:
            if not await scheduled_media.touch_existing(name):
                raise ScheduledPostRefusedError(_MEDIA_MISSING)
        # Reviving a settled post opens a new slot, so it answers to the same cap.
        if (
            post.state not in {"pending", "processing"}
            and await repo.count_open_for_account(
                account_id,
            )
            >= settings.scheduled_posts.max_pending_per_account
        ):
            raise ScheduledPostRefusedError(_PENDING_LIMIT)
        if not await repo.reschedule_post(post_id, account_id, run_at):
            raise ScheduledPostConflictError(post_id)
    _runtime.wake()
    await log_event(
        "INFO",
        "account_scheduled_post_rescheduled",
        account_id=account_id,
        extra={"kind": post.kind},
    )
    return to_read(await _require_post(account_id, post_id))


async def cancel_scheduled_post(account_id: str, post_id: str) -> ScheduledPostRead:
    post = await _require_post(account_id, post_id)
    if not await repo.cancel_post(post_id, account_id):
        raise ScheduledPostConflictError(post_id)
    _runtime.wake()
    await collect_media_garbage()
    await log_event(
        "INFO",
        "account_scheduled_post_cancelled",
        account_id=account_id,
        extra={"kind": post.kind},
    )
    return to_read(await _require_post(account_id, post_id))


async def scheduled_post_thumbnail(account_id: str, post_id: str) -> ProfileImage | None:
    """A small JPEG of the post's first image, or ``None`` (unknown, video, gone)."""
    post = await repo.fetch_post(post_id)
    if post is None or post.account_id != account_id or _thumb_url(post) is None:
        return None
    name = post.media_names[0]
    content = await scheduled_media.thumbnail(name)
    # The file name IS the content hash, so it is a stable validator as it is.
    return None if content is None else ProfileImage(content=content, etag=f'"{name}"')


async def collect_media_garbage() -> int:
    """Sweep stored files that no live post references any more."""
    async with scheduled_media.store_lock():
        referenced = await repo.referenced_media()
        return await scheduled_media.collect_garbage(
            referenced,
            settings.scheduled_posts.media_gc_grace_seconds,
        )
