"""Read-side result schemas — what the Telegram gateway returns for profile reads.

Kept separate from ``telegram_actions.py`` so the discriminated action union stays
focused on commands. These are plain result models, no discriminator.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TelegramProfileSnapshot(BaseModel):
    """The signed-in user's own profile as Telegram knows it right now."""

    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    phone: str | None = None
    bio: str | None = None
    # Raw image bytes for the current avatar (any format Telegram returns —
    # usually JPEG). ``None`` when the user has no avatar set.
    avatar_bytes: bytes | None = None


class TelegramStoryThumb(BaseModel):
    story_id: int
    kind: Literal["image", "video", "unknown"] = "unknown"
    caption: str | None = None
    thumb_bytes: bytes | None = None


class TelegramPinnedStories(BaseModel):
    items: list[TelegramStoryThumb] = Field(default_factory=list)


class TelegramMusicItem(BaseModel):
    file_id: int
    title: str | None = None
    performer: str | None = None
    duration_seconds: int | None = None
    # InputDocument requires all three fields to identify a Telegram document
    # for deletion. Empty defaults distinguish optimistic-add rows (synthetic
    # negative ``file_id``) that can't be removed via Telegram until refresh.
    access_hash: int = 0
    file_reference: bytes = b""


class TelegramProfileMusic(BaseModel):
    items: list[TelegramMusicItem] = Field(default_factory=list)
    # ``False`` when the installed Telethon version lacks the music TL methods —
    # the UI uses this to hide the music preview block entirely.
    supported: bool = True
