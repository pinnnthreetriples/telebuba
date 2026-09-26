"""Telegram chat and message contracts used by the account chat API."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003  # Pydantic resolves model fields at runtime.
from pathlib import Path  # noqa: TC003  # Pydantic resolves internal upload DTOs at runtime.
from typing import Literal

from pydantic import BaseModel, Field

ChatPeerType = Literal["user", "chat", "channel"]
ChatMediaKind = Literal[
    "image", "video", "audio", "voice", "video_note", "sticker", "document", "animation", "unknown"
]


class ChatMedia(BaseModel):
    kind: ChatMediaKind
    file_name: str | None = None
    mime_type: str | None = None
    size: int | None = None
    download_url: str


class ChatMessage(BaseModel):
    message_id: int
    text: str = ""
    date: datetime
    outgoing: bool
    sender_id: int | None = None
    media: list[ChatMedia] = Field(default_factory=list)


class ChatDialog(BaseModel):
    peer_type: ChatPeerType
    peer_id: str
    title: str
    username: str | None = None
    is_archived: bool
    unread_count: int
    last_message: ChatMessage | None = None


class ChatHistoryPage(BaseModel):
    items: list[ChatMessage]
    next_before_id: int | None = None


class ChatSendResult(BaseModel):
    items: list[ChatMessage]


class ChatUpload(BaseModel):
    path: Path
    file_name: str


class ChatReadRequest(BaseModel):
    max_message_id: int = Field(ge=1)


class ChatReadResult(BaseModel):
    acknowledged_up_to: int
