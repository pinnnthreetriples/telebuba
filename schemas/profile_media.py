from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

StoryMediaKind = Literal["image", "video"]
StoryPrivacyPreset = Literal["contacts", "close_friends", "public"]


class AccountProfilePhotoUpload(BaseModel):
    account_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    content: bytes = Field(min_length=1)


class AccountStoryUpload(BaseModel):
    account_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    content: bytes = Field(min_length=1)
    media_kind: StoryMediaKind
    caption: str | None = Field(default=None, max_length=1024)
    privacy_preset: StoryPrivacyPreset = "contacts"
    period_seconds: int = Field(default=86_400, ge=21_600, le=86_400)
    protect_content: bool = False


class AccountProfileMusicUpload(BaseModel):
    account_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    content: bytes = Field(min_length=1)
    title: str | None = Field(default=None, min_length=1)
    performer: str | None = Field(default=None, min_length=1)


class AccountProfileMusicRemove(BaseModel):
    """Unpin a single track from the account's saved profile music.

    All three Telegram identifiers are required — the read-side
    ``TelegramMusicItem`` always carries them after a real GetSavedMusic
    fetch. Optimistic-add rows have empty ``file_reference`` and must not
    reach this service (the UI guards them with a disabled delete button).
    """

    account_id: str = Field(min_length=1)
    file_id: int = Field(gt=0)
    access_hash: int
    file_reference: bytes = Field(min_length=1)
