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
