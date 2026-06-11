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


TelegramAction = Annotated[
    JoinChannel
    | LeaveChannel
    | PostComment
    | UpdateProfile
    | SetOnline
    | ReadChannel
    | ReactToPost
    | SendDirectMessage,
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
