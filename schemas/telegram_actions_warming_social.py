"""Warming actions toward other people: comment threads read, and the live side of DMs.

Прогрев 2.0 items 4.6 and 4.7, a sibling of ``schemas.telegram_actions_warming`` (file
size). Unlike the self-scoped extras there, the DM models DO carry a destination — the
dialogue partner — and are only ever dispatched from the dialogue turn, under every DM
gate (fleet pause, ``dm_min_age``, trust/readiness). Nothing here writes in a group:
the discussion read is a read, and it never joins the linked group.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class WarmReadDiscussion(BaseModel):
    """Read: open the comments under one channel post and mark them read.

    ``messages.getReplies`` + ``messages.readDiscussion`` on the channel post itself —
    no join of the linked discussion group. No linked group → skip ``no_discussion``.
    """

    action_type: Literal["warm_read_discussion"] = "warm_read_discussion"
    channel: str = Field(min_length=1)
    message_id: int = Field(ge=1)
    limit: int = Field(default=20, ge=1, le=100)


class _DmPeer(BaseModel):
    # The partner, as ``SendDirectMessage`` addresses them (phone teaches a cold session).
    user_id: int
    peer_phone: str | None = None


class WarmDmReact(_DmPeer):
    """Write: react to the partner's newest message instead of answering it."""

    action_type: Literal["warm_dm_react"] = "warm_dm_react"
    emoji: str = Field(min_length=1, max_length=8)


class WarmDmSticker(_DmPeer):
    """Write: send a sticker for ``emoticon`` from the account's installed sets.

    ``messages.getStickers`` by emoticon; no installed sticker for it → skip ``no_sticker``.
    """

    action_type: Literal["warm_dm_sticker"] = "warm_dm_sticker"
    emoticon: str = Field(min_length=1, max_length=8)
    # Which of the matching stickers (modulo their count); randomness is the service's.
    pick: int = Field(default=0, ge=0)


class WarmDmForward(_DmPeer):
    """Write: forward a post the account just read to the partner, with a short word."""

    action_type: Literal["warm_dm_forward"] = "warm_dm_forward"
    channel: str = Field(min_length=1)
    message_id: int = Field(ge=1)
    # «смотри» / «mira»: sent right after the forward; empty sends the forward alone.
    comment: str = Field(default="", max_length=40)


# The four, as one member of ``TelegramAction`` (flattened by the union, discriminated
# on ``action_type`` like every other action).
WarmingSocialAction = WarmReadDiscussion | WarmDmReact | WarmDmSticker | WarmDmForward
