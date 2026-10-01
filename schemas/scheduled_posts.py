"""Contracts for timed profile-photo / story publishing.

A post is uploaded once as media (``ScheduledMediaUploaded.media_id``) and then
scheduled per account by JSON, so a bulk run sends a file over the wire once.
The publish-time window depends on settings, so it is enforced by the service,
not here: ``schemas`` may not read ``core.config``.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - pydantic resolves field annotations at runtime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, Field

from schemas.profile_media import (  # noqa: TC001 - pydantic resolves them at runtime
    StoryMediaKind,
    StoryPrivacyPreset,
)

# ``<sha256 hex><suffix>``: the store's own file name, handed back as the id.
MEDIA_ID_PATTERN = r"^[0-9a-f]{64}\.(?:jpg|jpeg|png|webp|mp4|mov)$"
_KEY_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"

MediaId = Annotated[str, Field(pattern=MEDIA_ID_PATTERN)]
ScheduledPostKind = Literal["photo", "story"]
ScheduledPostState = Literal[
    "pending",
    "processing",
    "done",
    "failed",
    "cancelled",
    "missed",
    # Telegram may or may not have applied it (the answer was lost): never retried
    # automatically, because retrying could publish it twice.
    "ambiguous",
]
ScheduledPostStage = Literal["queued", "dispatching"]
# Refusals raised by the scheduling policy itself; the SPA translates them.
ScheduledPostRefusalCode = Literal[
    "scheduled_run_at_out_of_range",
    "scheduled_not_reschedulable",
    "scheduled_media_missing",
    "scheduled_media_invalid",
    "scheduled_media_kind_mismatch",
    "scheduled_pending_limit",
    "scheduled_store_full",
    "scheduled_too_many_images",
    "scheduled_collage_layout_invalid",
    "scheduled_post_not_found",
    "scheduled_post_invalid",
]


class ScheduledStoryOptions(BaseModel):
    """Everything a story needs besides its media, frozen at schedule time."""

    media_kind: StoryMediaKind = "image"
    caption: str | None = Field(default=None, max_length=1024)
    privacy_preset: StoryPrivacyPreset = "contacts"
    protect_content: bool = False
    collage_layout: str | None = Field(default=None, max_length=64)
    period_seconds: int = Field(default=86_400, ge=21_600, le=86_400)


class ScheduledPost(BaseModel):
    """One durable row as the repository returns it (internal, never served)."""

    post_id: str
    account_id: str
    kind: ScheduledPostKind
    state: ScheduledPostState
    stage: ScheduledPostStage
    scheduled_for_unix: int
    next_attempt_unix: int
    attempts: int = 0
    media_names: list[str]
    filename: str | None = None
    story: ScheduledStoryOptions | None = None
    batch_id: str | None = None
    client_key: str | None = None
    error_code: str | None = None
    story_id: int | None = None
    created_at: str
    updated_unix: int
    finished_unix: int | None = None


class ScheduledMediaUploaded(BaseModel):
    media_id: str
    media_kind: StoryMediaKind
    size_bytes: int


class _ScheduleBase(BaseModel):
    run_at: AwareDatetime
    # Display only: the stored file is named by its content hash.
    filename: str | None = Field(default=None, max_length=255)
    # A bulk run's retry of the same row must not create a second post.
    batch_id: str | None = Field(default=None, pattern=_KEY_PATTERN)
    client_key: str | None = Field(default=None, pattern=_KEY_PATTERN)


class SchedulePhotoRequest(_ScheduleBase):
    media_id: MediaId


class ScheduleStoryRequest(_ScheduleBase):
    # First = the story's media; the rest = the collage's images 2..N.
    media_ids: list[MediaId] = Field(min_length=1, max_length=6)
    caption: str | None = Field(default=None, max_length=1024)
    privacy_preset: StoryPrivacyPreset = "contacts"
    protect_content: bool = False
    collage_layout: str | None = Field(default=None, max_length=64)
    period_seconds: int = Field(default=86_400, ge=21_600, le=86_400)


class ScheduledPostReschedule(BaseModel):
    run_at: AwareDatetime


class ScheduledPostRead(BaseModel):
    post_id: str
    kind: ScheduledPostKind
    state: ScheduledPostState
    run_at: datetime
    next_attempt_at: datetime
    finished_at: datetime | None = None
    attempts: int
    error_code: str | None = None
    filename: str | None = None
    media_kind: StoryMediaKind
    media_count: int
    caption: str | None = None
    privacy_preset: StoryPrivacyPreset | None = None
    story_id: int | None = None
    # Relative URL of a small preview, or None once the media is gone / is a video.
    thumb_url: str | None = None


class ScheduledPostList(BaseModel):
    items: list[ScheduledPostRead]
    # The server's clock, so the SPA can tell "overdue" from a skewed browser clock.
    server_now: datetime
