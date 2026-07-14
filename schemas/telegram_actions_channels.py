"""Channel-management Telegram actions.

The own-channel cluster of ``TelegramAction`` / ``TelegramReadAction`` members:
create/edit/delete a broadcast channel the account owns, manage its photo, and
publish/edit/delete posts in it. Split out of ``telegram_actions.py`` (like the
profile-media sibling) to keep that module under the file-size cap; the
discriminated unions there import these names back, so external callers keep
importing every action from ``schemas.telegram_actions`` unchanged.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

CHANNEL_TITLE_MAX_LENGTH = 128
CHANNEL_ABOUT_MAX_LENGTH = 255
CHANNEL_POST_TEXT_MAX_LENGTH = 4096
CHANNEL_POST_CAPTION_MAX_LENGTH = 1024
# Telegram message ids are int32 — anything above cannot exist, and letting a
# larger value through would blow up in TL struct packing instead of a 400.
CHANNEL_POST_ID_MAX = 2**31 - 1
# Telegram public-handle rules: 5..32 chars, starts with a letter,
# letters/digits/underscore after that.
CHANNEL_USERNAME_PATTERN = r"^[A-Za-z][A-Za-z0-9_]{4,31}$"


class CreateChannel(BaseModel):
    """Create a broadcast channel owned by the account.

    ``username`` is optional — set, it makes the channel public under that
    handle. The gateway pre-checks availability before creating, so the
    deterministic occupied-handle case fails before anything exists; if the
    post-create username assignment still fails, the created (private)
    channel's id is carried on the error instead of being silently orphaned.
    """

    action_type: Literal["channel_create"] = "channel_create"
    title: str = Field(min_length=1, max_length=CHANNEL_TITLE_MAX_LENGTH)
    about: str = Field(default="", max_length=CHANNEL_ABOUT_MAX_LENGTH)
    username: str | None = Field(default=None, pattern=CHANNEL_USERNAME_PATTERN)


class EditChannel(BaseModel):
    """Edit a channel's title and/or about. ``None`` = unchanged, ``""`` clears about."""

    action_type: Literal["channel_edit"] = "channel_edit"
    channel_id: int = Field(gt=0)
    title: str | None = Field(default=None, min_length=1, max_length=CHANNEL_TITLE_MAX_LENGTH)
    about: str | None = Field(default=None, max_length=CHANNEL_ABOUT_MAX_LENGTH)

    @model_validator(mode="after")
    def _check_any_field(self) -> EditChannel:
        if self.title is None and self.about is None:
            msg = "at least one of title/about must be set"
            raise ValueError(msg)
        return self


class SetChannelPhoto(BaseModel):
    action_type: Literal["channel_set_photo"] = "channel_set_photo"
    channel_id: int = Field(gt=0)
    filename: str = Field(min_length=1)
    content: bytes = Field(min_length=1)


class DeleteChannel(BaseModel):
    action_type: Literal["channel_delete"] = "channel_delete"
    channel_id: int = Field(gt=0)


class PublishChannelPost(BaseModel):
    """Publish a post: text-only, or a photo/video with an optional caption.

    The media triple (``filename``/``content``/``media_kind``) is all-or-none.
    Without media the ``text`` is the message body (required, up to 4096);
    with media it becomes the caption (Telegram caps captions at 1024).
    """

    action_type: Literal["channel_post_publish"] = "channel_post_publish"
    channel_id: int = Field(gt=0)
    text: str = Field(default="", max_length=CHANNEL_POST_TEXT_MAX_LENGTH)
    filename: str | None = Field(default=None, min_length=1)
    content: bytes | None = Field(default=None, min_length=1)
    media_kind: Literal["photo", "video"] | None = None

    @model_validator(mode="after")
    def _check_media(self) -> PublishChannelPost:
        provided = [
            self.filename is not None,
            self.content is not None,
            self.media_kind is not None,
        ]
        if any(provided) and not all(provided):
            msg = "filename, content and media_kind must be provided together"
            raise ValueError(msg)
        if not any(provided) and not self.text:
            msg = "a post without media must carry text"
            raise ValueError(msg)
        if all(provided) and len(self.text) > CHANNEL_POST_CAPTION_MAX_LENGTH:
            msg = "media caption must be at most 1024 characters"
            raise ValueError(msg)
        return self


class EditChannelPost(BaseModel):
    action_type: Literal["channel_post_edit"] = "channel_post_edit"
    channel_id: int = Field(gt=0)
    post_id: int = Field(gt=0, le=CHANNEL_POST_ID_MAX)
    text: str = Field(min_length=1, max_length=CHANNEL_POST_TEXT_MAX_LENGTH)


class DeleteChannelPost(BaseModel):
    action_type: Literal["channel_post_delete"] = "channel_post_delete"
    channel_id: int = Field(gt=0)
    post_id: int = Field(gt=0, le=CHANNEL_POST_ID_MAX)


class ListOwnChannels(BaseModel):
    """Read-only: list the broadcast channels the account CREATED (owns)."""

    action_type: Literal["list_own_channels"] = "list_own_channels"
    limit: int = Field(default=50, ge=1, le=200)


class GetOwnChannel(BaseModel):
    """Read-only: one owned channel's detail (title/username/about/participants)."""

    action_type: Literal["get_own_channel"] = "get_own_channel"
    channel_id: int = Field(gt=0)


class ListChannelPosts(BaseModel):
    """Read-only: recent posts of an owned channel, newest first.

    ``offset_id`` pages backwards: only posts with an id strictly below it are
    returned (Telegram's native message-history cursor).
    """

    action_type: Literal["list_channel_posts"] = "list_channel_posts"
    channel_id: int = Field(gt=0)
    limit: int = Field(default=20, ge=1, le=100)
    offset_id: int = Field(default=0, ge=0, le=CHANNEL_POST_ID_MAX)


class CheckChannelUsername(BaseModel):
    """Read-only: is this public handle available for a new channel?"""

    action_type: Literal["check_channel_username"] = "check_channel_username"
    username: str = Field(pattern=CHANNEL_USERNAME_PATTERN)


class TelegramOwnChannel(BaseModel):
    """Gateway output: one owned channel in the list view."""

    channel_id: int
    title: str
    username: str | None = None
    participants_count: int | None = None


class TelegramOwnChannels(BaseModel):
    items: list[TelegramOwnChannel]


class TelegramOwnChannelDetail(TelegramOwnChannel):
    """Gateway output for ``GetOwnChannel`` — the list row plus the about text."""

    about: str = ""


class TelegramChannelPost(BaseModel):
    """Gateway output: one channel post (id, date, text/caption, media kind, views)."""

    post_id: int
    date_unix: int
    text: str = ""
    media_kind: Literal["none", "photo", "video", "other"]
    views: int | None = None


class TelegramChannelPosts(BaseModel):
    items: list[TelegramChannelPost]


class ChannelUsernameCheck(BaseModel):
    """Gateway output for ``CheckChannelUsername``.

    ``code`` carries the stable refusal reason when ``available`` is False
    (``channel_username_invalid`` / ``channel_username_occupied``) — ``None``
    when the handle is simply free.
    """

    available: bool
    code: str | None = None
