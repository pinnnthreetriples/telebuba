"""Realtime inbox notifications sent over the authenticated SSE stream."""

from __future__ import annotations

from typing import Literal

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
    # Only for Telegram's service user (777000): whether the message carries a login code.
    # The text itself never leaves the gateway, and this field
    # stays out of the serialized event (the SSE stream and the OpenAPI body).
    service_kind: Literal["login_code", "other"] | None = Field(default=None, exclude=True)
