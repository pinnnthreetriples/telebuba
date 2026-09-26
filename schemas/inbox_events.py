"""Realtime inbox notifications sent over the authenticated SSE stream."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from schemas.chats import (
    ChatPeerType,  # noqa: TC001 - Pydantic resolves this annotation at runtime.
)


class InboxMessageReceived(BaseModel):
    """A refetch hint for one newly received Telegram message."""

    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1)
    peer_type: ChatPeerType
    peer_id: str = Field(min_length=1)
    message_id: int = Field(gt=0)
