"""Typed Telegram actions.

Pydantic-described "do X on this account". Services and features never call
``client.send_message(...)`` directly — they build one of these classes and
hand it to ``core.telegram_client.execute(account_id, action)``.

Discriminator: ``action_type`` literal. First-cut set covers the actions
warming will need.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class JoinChannel(BaseModel):
    action_type: Literal["join_channel"] = "join_channel"
    channel: str = Field(min_length=1)


class LeaveChannel(BaseModel):
    action_type: Literal["leave_channel"] = "leave_channel"
    channel: str = Field(min_length=1)


class PostComment(BaseModel):
    action_type: Literal["post_comment"] = "post_comment"
    chat_id: int
    text: str = Field(min_length=1)


class UpdateProfile(BaseModel):
    action_type: Literal["update_profile"] = "update_profile"
    first_name: str = Field(min_length=1)
    last_name: str | None = None
    username: str | None = None
    bio: str | None = None


class SetOnline(BaseModel):
    """Flip the account's presence — warming uses it to look "active"."""

    action_type: Literal["set_online"] = "set_online"
    online: bool = True


class ReadChannel(BaseModel):
    """Fetch recent posts and mark them read — emulates a human reading a feed."""

    action_type: Literal["read_channel"] = "read_channel"
    channel: str = Field(min_length=1)
    message_limit: int = Field(default=15, ge=1, le=100)


class ReactToPost(BaseModel):
    """React to a random recent post with one of the candidate emojis."""

    action_type: Literal["react_to_post"] = "react_to_post"
    channel: str = Field(min_length=1)
    reactions: list[str] = Field(min_length=1)
    message_limit: int = Field(default=20, ge=1, le=100)


class SendDirectMessage(BaseModel):
    """Send a private message to another account — drives inter-account chat."""

    action_type: Literal["send_dm"] = "send_dm"
    user_id: int
    text: str = Field(min_length=1)


class SetProfilePhoto(BaseModel):
    action_type: Literal["set_profile_photo"] = "set_profile_photo"
    filename: str = Field(min_length=1)
    content: bytes = Field(min_length=1)


class PostStory(BaseModel):
    action_type: Literal["post_story"] = "post_story"
    filename: str = Field(min_length=1)
    content: bytes = Field(min_length=1)
    media_kind: Literal["image", "video"]
    caption: str | None = Field(default=None, max_length=1024)
    privacy_preset: Literal["contacts", "close_friends", "public"] = "contacts"
    period_seconds: int = Field(default=86_400, ge=21_600, le=86_400)
    protect_content: bool = False


class AddProfileMusic(BaseModel):
    action_type: Literal["add_profile_music"] = "add_profile_music"
    filename: str = Field(min_length=1)
    content: bytes = Field(min_length=1)
    title: str | None = Field(default=None, min_length=1)
    performer: str | None = Field(default=None, min_length=1)


class RemoveProfileMusic(BaseModel):
    """Unpins one track from the account's saved profile music.

    All three identifier fields are required — Telethon's ``InputDocument``
    refuses partial refs. ``file_id`` alone is not enough; the read-side
    ``TelegramMusicItem`` carries ``access_hash`` and ``file_reference`` for
    exactly this reason.
    """

    action_type: Literal["remove_profile_music"] = "remove_profile_music"
    file_id: int = Field(gt=0)
    access_hash: int
    file_reference: bytes = Field(min_length=1)


class GetUserProfile(BaseModel):
    """Read-only: pull the signed-in user's own current profile state."""

    action_type: Literal["get_user_profile"] = "get_user_profile"


class ListPinnedStories(BaseModel):
    """Read-only: list the account's pinned-on-profile stories."""

    action_type: Literal["list_pinned_stories"] = "list_pinned_stories"
    limit: int = Field(default=20, ge=1, le=100)


class ListProfileMusic(BaseModel):
    """Read-only: list the music shown on the account's profile.

    Gracefully degrades when the installed Telethon version lacks the music TL
    methods — the gateway returns an empty list with ``supported=False``.
    """

    action_type: Literal["list_profile_music"] = "list_profile_music"


TelegramAction = Annotated[
    JoinChannel
    | LeaveChannel
    | PostComment
    | UpdateProfile
    | SetOnline
    | ReadChannel
    | ReactToPost
    | SendDirectMessage
    | SetProfilePhoto
    | PostStory
    | AddProfileMusic
    | RemoveProfileMusic,
    Field(discriminator="action_type"),
]

TelegramReadAction = Annotated[
    GetUserProfile | ListPinnedStories | ListProfileMusic,
    Field(discriminator="action_type"),
]


ActionStatus = Literal[
    "ok",
    "flood_wait",
    "slow_mode_wait",
    "premium_wait",
    "peer_flood",
    "failed",
]


class ActionResult(BaseModel):
    """Outcome of one ``execute`` call."""

    status: ActionStatus
    action_type: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    message_id: int | None = None
    flood_wait_seconds: int | None = None
    error_type: str | None = None
    error_message: str | None = None
