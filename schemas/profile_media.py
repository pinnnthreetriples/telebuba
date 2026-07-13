from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

# Same charset guard as every other account_id entry point (see the
# rationale next to the pattern's definition in schemas.accounts).
from schemas.accounts import _ACCOUNT_ID_PATTERN

# Telegram photo_id / file_id / access_hash are int64 (~19 digits), past JS's
# 2^53 safe-integer limit — as JSON numbers the SPA would silently round them,
# so a remove/set-main round-trip sends back the wrong InputPhoto and Telegram
# no-ops it. They cross the JSON boundary as decimal strings instead (access_hash
# is signed, so the minus sign is allowed).
_Int64Str = Annotated[str, Field(pattern=r"^-?\d+$")]

StoryMediaKind = Literal["image", "video"]
StoryPrivacyPreset = Literal["contacts", "close_friends", "public"]


class AccountProfilePhotoUpload(BaseModel):
    account_id: str = Field(min_length=1, pattern=_ACCOUNT_ID_PATTERN)
    filename: str = Field(min_length=1)
    content: bytes = Field(min_length=1)


class AccountStoryUpload(BaseModel):
    account_id: str = Field(min_length=1, pattern=_ACCOUNT_ID_PATTERN)
    filename: str = Field(min_length=1)
    content: bytes = Field(min_length=1)
    media_kind: StoryMediaKind
    caption: str | None = Field(default=None, max_length=1024)
    privacy_preset: StoryPrivacyPreset = "contacts"
    period_seconds: int = Field(default=86_400, ge=21_600, le=86_400)
    protect_content: bool = False
    # Collage images 2..N (``content``/``filename`` is image #1). Empty = the
    # existing single-photo/video path; non-empty = a multi-photo collage. The
    # count cap + per-image validation are enforced in the service.
    extra_images: list[bytes] = Field(default_factory=list)
    collage_layout: str | None = None


class AccountProfileMusicUpload(BaseModel):
    account_id: str = Field(min_length=1, pattern=_ACCOUNT_ID_PATTERN)
    filename: str = Field(min_length=1)
    content: bytes = Field(min_length=1)
    title: str | None = Field(default=None, min_length=1)
    performer: str | None = Field(default=None, min_length=1)


class AccountProfilePhotoRemove(BaseModel):
    """Drop a single photo from the account's profile-photo history.

    All three Telegram identifiers come from the canonical
    ``TelegramProfilePhoto`` snapshot — synthetic optimistic-add rows have
    empty ``file_reference`` and must not reach this service.
    """

    account_id: str = Field(min_length=1, pattern=_ACCOUNT_ID_PATTERN)
    photo_id: int = Field(gt=0)
    access_hash: int
    file_reference: bytes = Field(min_length=1)


class AccountProfilePhotoSetMain(BaseModel):
    """Promote an existing history photo to the account's current avatar.

    Same identifier triple as :class:`AccountProfilePhotoRemove` — the photo
    is already in the account's history, so only its ``InputPhoto`` is needed.
    """

    account_id: str = Field(min_length=1, pattern=_ACCOUNT_ID_PATTERN)
    photo_id: int = Field(gt=0)
    access_hash: int
    file_reference: bytes = Field(min_length=1)


class AccountStoryRemove(BaseModel):
    """Delete one story (active and/or pinned) from the account.

    ``story_id`` comes from the live snapshot the UI is displaying. Telegram
    silently drops unknown IDs from the result vector, so callers can't tell
    apart "already gone" from "successfully removed" — both paths land here
    as ``status='ok'``.
    """

    account_id: str = Field(min_length=1, pattern=_ACCOUNT_ID_PATTERN)
    story_id: int = Field(gt=0)


class AccountStoryPin(BaseModel):
    """Pin a story to the profile (``pinned=True``, kept forever) or unpin it.

    ``story_id`` comes from the live snapshot; ``pinned`` is the target state so
    the UI toggle is idempotent regardless of the story's current state.
    """

    account_id: str = Field(min_length=1, pattern=_ACCOUNT_ID_PATTERN)
    story_id: int = Field(gt=0)
    pinned: bool


class AccountProfileMusicRemove(BaseModel):
    """Unpin a single track from the account's saved profile music.

    All three Telegram identifiers are required — the read-side
    ``TelegramMusicItem`` always carries them after a real GetSavedMusic
    fetch. Optimistic-add rows have empty ``file_reference`` and must not
    reach this service (the UI guards them with a disabled delete button).
    """

    account_id: str = Field(min_length=1, pattern=_ACCOUNT_ID_PATTERN)
    file_id: int = Field(gt=0)
    access_hash: int
    file_reference: bytes = Field(min_length=1)


# The *View models are the JSON-safe edit-profile snapshot: file_reference (raw
# bytes in the live snapshot) travels as base64, thumbnails as a cacheable
# image-endpoint URL (see ProfileImage / account_profile_image).
class ProfilePhotoView(BaseModel):
    photo_id: str  # int64 as string (see _Int64Str)
    access_hash: str
    file_reference: str = Field(min_length=1)  # base64
    thumb_url: str | None = None
    # True for the photo Telegram currently shows as the avatar (matched by id
    # against the authoritative ``current_photo_id``), so the SPA marks "main"
    # by identity rather than guessing index 0.
    is_main: bool = False


class ProfileStoryView(BaseModel):
    story_id: int
    kind: str = "unknown"
    caption: str | None = None
    privacy_preset: str = "unknown"
    is_pinned: bool = False
    # Story view count from Telegram (``None`` when the account can't see its
    # own story views, e.g. the story is expired and unpinned).
    views: int | None = None
    # Total reactions left on the story (``None`` under the same conditions).
    reactions: int | None = None
    thumb_url: str | None = None


class ProfileMusicView(BaseModel):
    file_id: str  # int64 as string
    title: str | None = None
    performer: str | None = None
    access_hash: str = "0"
    file_reference: str = ""  # base64 (empty for optimistic-add rows)


class AccountProfileView(BaseModel):
    """JSON-safe live profile for the edit-profile modal."""

    error: str | None = None
    # Live profile text pulled from Telegram, so «Обновить» refreshes the header +
    # Текст-tab fields (not just the media). ``None`` when the live fetch failed.
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    bio: str | None = None
    photos: list[ProfilePhotoView] = Field(default_factory=list)
    stories: list[ProfileStoryView] = Field(default_factory=list)
    music: list[ProfileMusicView] = Field(default_factory=list)
    music_supported: bool = True


class ProfileImage(BaseModel):
    """Raw thumbnail bytes + cache metadata served by the image endpoints.

    The service returns this model (Pydantic boundary); the API layer turns it
    into a binary Response with cache headers.
    """

    content: bytes = Field(min_length=1)
    media_type: str = "image/jpeg"
    etag: str = Field(min_length=1)  # content hash, used for If-None-Match revalidation


class StoryRemoveRequest(BaseModel):
    story_id: int = Field(gt=0)


class StoryPinRequest(BaseModel):
    story_id: int = Field(gt=0)
    pinned: bool


class MusicRemoveRequest(BaseModel):
    file_id: _Int64Str
    access_hash: _Int64Str
    file_reference: str = Field(min_length=1)  # base64 from the view


class PhotoRemoveRequest(BaseModel):
    photo_id: _Int64Str
    access_hash: _Int64Str
    file_reference: str = Field(min_length=1)  # base64 from the view


class PhotoMainRequest(BaseModel):
    """Promote an existing profile photo to the current avatar.

    Same ``InputPhoto`` triple as :class:`PhotoRemoveRequest` — the id fields
    arrive as int64 strings from the view (see :data:`_Int64Str`).
    """

    photo_id: _Int64Str
    access_hash: _Int64Str
    file_reference: str = Field(min_length=1)  # base64 from the view
