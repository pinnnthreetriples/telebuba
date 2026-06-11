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


TelegramAction = Annotated[
    JoinChannel
    | LeaveChannel
    | PostComment
    | UpdateProfile
    | SetProfilePhoto
    | PostStory
    | AddProfileMusic,
    Field(discriminator="action_type"),
]


ActionStatus = Literal["ok", "flood_wait", "failed"]


class ActionResult(BaseModel):
    """Outcome of one ``execute`` call."""

    status: ActionStatus
    action_type: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    message_id: int | None = None
    flood_wait_seconds: int | None = None
    error_type: str | None = None
    error_message: str | None = None
