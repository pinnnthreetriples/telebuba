"""Chat-broadcast Telegram actions — write into groups, forward a post, join a folder chat.

A sibling of :mod:`schemas.telegram_actions` for the file-size cap; every name is
re-imported there so the discriminated unions stay in one place. The write actions share
the ``broadcast_`` prefix, which is how ``core.telegram_client._action_families`` routes
the whole family to one dispatcher without a new arm in ``_dispatch_action``.

``chat`` follows :class:`schemas.telegram_actions_chat.ReadChatMessages`: an all-digit
value is the raw positive id the SENDING account resolved (ids are per-account session
state), anything else a public username.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Telegram's own ceilings: 4096 characters for a message, 1024 for a media caption.
BROADCAST_TEXT_MAX = 4096
BROADCAST_CAPTION_MAX = 1024
# "Typing…" is a courtesy, not a delay the operator tunes; the gateway caps it so a long
# text cannot hold the account lock for a minute.
BROADCAST_TYPING_MAX_SECONDS = 10.0


class BroadcastSendMessage(BaseModel):
    """Send text, or a photo with the text as its caption, into one group.

    ``typing_seconds`` > 0 shows "typing…" in the chat for that long first. The photo
    travels as bytes (like ``PublishChannelPost``) so the gateway never reads the disk.
    """

    action_type: Literal["broadcast_send_message"] = "broadcast_send_message"
    chat: str = Field(min_length=1)
    text: str = Field(default="", max_length=BROADCAST_TEXT_MAX)
    photo: bytes | None = None
    photo_name: str = "photo.jpg"
    typing_seconds: float = Field(default=0.0, ge=0.0, le=BROADCAST_TYPING_MAX_SECONDS)


class BroadcastForwardPost(BaseModel):
    """Forward one channel post into a group whole, with its "Forwarded from" line."""

    action_type: Literal["broadcast_forward_post"] = "broadcast_forward_post"
    chat: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    message_id: int = Field(gt=0)


class BroadcastJoinChatlist(BaseModel):
    """Join ONE chat of a shared folder (``t.me/addlist/<slug>``).

    The folder link is the only way in for a private chat of a folder, and the peer's
    access hash is per account — so the gateway re-checks the invite with the joining
    account and joins just this one peer, never the whole folder at once.
    """

    action_type: Literal["broadcast_join_chatlist"] = "broadcast_join_chatlist"
    slug: str = Field(min_length=1, max_length=64)
    peer_id: int = Field(gt=0)


class ListWritableGroups(BaseModel):
    """Read-only: the groups this account is in and may write to."""

    action_type: Literal["list_writable_groups"] = "list_writable_groups"
    limit: int = Field(default=500, ge=1, le=2000)


class CheckChatlist(BaseModel):
    """Read-only: the chats behind a shared folder link, as this account sees them."""

    action_type: Literal["check_chatlist"] = "check_chatlist"
    slug: str = Field(min_length=1, max_length=64)


class BroadcastGroup(BaseModel):
    """One group an account can write to — or one chat of a folder."""

    peer_id: int = Field(gt=0)
    title: str
    username: str | None = None


class WritableGroupsResult(BaseModel):
    """Gateway output for ``ListWritableGroups``; channels and admin-only groups are counted."""

    groups: list[BroadcastGroup]
    channels_skipped: int = 0
    admin_only_skipped: int = 0


class ChatlistResult(BaseModel):
    """Gateway output for ``CheckChatlist``: the folder's groups; broadcast channels counted."""

    title: str = ""
    groups: list[BroadcastGroup]
    channels_skipped: int = 0
