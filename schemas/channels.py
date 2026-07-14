"""Wire contracts for the account channel-management endpoints.

Requests the SPA sends and views it renders. Channel ids are Telegram int64s
(~19 digits, past JS's 2^53 safe-integer limit) — as JSON numbers the SPA
would silently round them, so they cross the JSON boundary as decimal strings
(same rationale as :mod:`schemas.profile_media`).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from schemas.telegram_actions_channels import (
    CHANNEL_ABOUT_MAX_LENGTH,
    CHANNEL_POST_TEXT_MAX_LENGTH,
    CHANNEL_TITLE_MAX_LENGTH,
    CHANNEL_USERNAME_PATTERN,
)

_Int64Str = Annotated[str, Field(pattern=r"^-?\d+$")]

ChannelPostMediaKind = Literal["none", "photo", "video", "other"]


class ChannelCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=CHANNEL_TITLE_MAX_LENGTH)
    about: str = Field(default="", max_length=CHANNEL_ABOUT_MAX_LENGTH)
    username: str | None = Field(default=None, pattern=CHANNEL_USERNAME_PATTERN)


class ChannelUpdateRequest(BaseModel):
    """Partial edit: ``None`` leaves a field unchanged, ``""`` clears the about.

    "At least one field set" is enforced by the ``EditChannel`` action model
    the service builds — a no-op request fails there with a 400.
    """

    title: str | None = Field(default=None, min_length=1, max_length=CHANNEL_TITLE_MAX_LENGTH)
    about: str | None = Field(default=None, max_length=CHANNEL_ABOUT_MAX_LENGTH)


class ChannelPostEditRequest(BaseModel):
    text: str = Field(min_length=1, max_length=CHANNEL_POST_TEXT_MAX_LENGTH)


class ChannelView(BaseModel):
    """One owned channel in the list view (id as int64-string, see module doc)."""

    channel_id: _Int64Str
    title: str
    username: str | None = None
    participants_count: int | None = None


class ChannelDetailView(ChannelView):
    about: str = ""


class ChannelPostView(BaseModel):
    """One channel post. ``post_id`` is a Telegram message id (int32-safe)."""

    post_id: int
    date_unix: int
    text: str = ""
    media_kind: ChannelPostMediaKind = "none"
    views: int | None = None


class ChannelUsernameCheckView(BaseModel):
    """Availability verdict for a public handle.

    ``code`` is the stable refusal reason (``channel_username_invalid`` /
    ``channel_username_occupied``) when ``available`` is False; the SPA
    translates it (non-negotiable #12).
    """

    available: bool
    code: str | None = None
