"""User-parser reads — who is in a chat, who writes in it, who comments under a channel.

Its own module because ``telegram_actions`` is at the file-size cap; ``telegram_actions``
re-imports the three actions so the read union can name them.

What a user record carries is deliberately narrow: the public profile facts the parser's
filters read and nothing else. No phone, no ``access_hash``, no bio — the first two are
per-account secrets of the session that saw them, and none of them is something the
operator filters on.

Message text rides the page only so the service can match keywords and measure a
comment's length; the service never stores it.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - pydantic resolves the annotation at runtime
from typing import Literal

from pydantic import BaseModel, Field

# Telegram reports "last seen" in buckets only. ``hidden`` is a user with no status at
# all (bots, and the accounts Telegram answers with nothing); a user who hides the exact
# time still lands in ``recently``/``week``/``month``, which is what Telegram shows.
LastSeenBucket = Literal["online", "recently", "week", "month", "long", "hidden"]

# Telegram's own page ceilings: 200 for ``channels.getParticipants``, 100 for history
# and replies. A literal, not config: ``schemas/`` may not read ``core``.
PARTICIPANTS_PAGE_MAX = 200
HISTORY_PAGE_MAX = 100


class ParsedTelegramUser(BaseModel):
    """One user as a parser filter sees them."""

    user_id: int
    username: str | None = None
    first_name: str = ""
    last_name: str = ""
    is_bot: bool = False
    is_deleted: bool = False
    is_scam: bool = False
    is_fake: bool = False
    is_premium: bool = False
    has_photo: bool = False
    has_stories: bool = False
    last_seen: LastSeenBucket = "hidden"


class ReadChatParticipants(BaseModel):
    """One page of a group's member list, or of its admins.

    The first page (``offset`` 0) also asks whether the list is hidden: a group can hide
    its members from non-admins, and Telegram then answers with an empty or admins-only
    list that would read as "nobody is here".
    """

    action_type: Literal["read_chat_participants"] = "read_chat_participants"
    chat: str = Field(min_length=1)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=PARTICIPANTS_PAGE_MAX, ge=1, le=PARTICIPANTS_PAGE_MAX)
    admins: bool = False


class ReadChatParticipantsResult(BaseModel):
    """A page of members; ``total`` is what Telegram says the chat has, when it says."""

    users: list[ParsedTelegramUser]
    total: int | None = None
    hidden: bool = False


class ReadChatHistoryAuthors(BaseModel):
    """One page of a chat's history, newest first, with the authors' profiles.

    ``offset_id`` 0 starts at the newest message; a later page passes the oldest id the
    previous one returned. ``min_date`` ends the walk: messages older than it are dropped
    and ``done`` is set. ``top_msg_id`` reads one forum topic (``t.me/chat/123``).
    Also reads a CHANNEL's posts, whose ``replies`` say how many comments each has.
    """

    action_type: Literal["read_chat_history_authors"] = "read_chat_history_authors"
    chat: str = Field(min_length=1)
    offset_id: int = Field(default=0, ge=0)
    limit: int = Field(default=HISTORY_PAGE_MAX, ge=1, le=HISTORY_PAGE_MAX)
    min_date: datetime | None = None
    top_msg_id: int | None = Field(default=None, ge=1)


class ChatHistoryMessage(BaseModel):
    """One message of a page. ``sender_id`` is ``None`` for a channel-signed post."""

    message_id: int
    sender_id: int | None = None
    date: datetime
    is_reply: bool = False
    is_forward: bool = False
    # Comment count under a channel post; ``None`` where comments are off or n/a.
    replies: int | None = None
    text: str = ""


class ReadChatHistoryAuthorsResult(BaseModel):
    """Messages newest first, the users who wrote them, and whether the walk is over."""

    messages: list[ChatHistoryMessage]
    users: list[ParsedTelegramUser]
    done: bool = False


class ReadChannelPostReplies(BaseModel):
    """One page of the comments under a channel post, newest first, with their authors."""

    action_type: Literal["read_channel_post_replies"] = "read_channel_post_replies"
    channel: str = Field(min_length=1)
    post_id: int = Field(ge=1)
    offset_id: int = Field(default=0, ge=0)
    limit: int = Field(default=HISTORY_PAGE_MAX, ge=1, le=HISTORY_PAGE_MAX)


class ReadChannelPostRepliesResult(BaseModel):
    """Comments newest first and their authors."""

    messages: list[ChatHistoryMessage]
    users: list[ParsedTelegramUser]
