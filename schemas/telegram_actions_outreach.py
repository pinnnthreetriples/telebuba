"""The outreach family (``outreach_*``): writes the bulk-message run makes besides the send."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class OutreachSetTyping(BaseModel):
    """Show "typing…" in the chat with one recipient, as ``SendChatMessage`` addresses it.

    One ``messages.setTyping``: Telegram shows it for about five seconds or until the
    next message arrives, so the caller waits its few seconds and then sends.
    """

    action_type: Literal["outreach_set_typing"] = "outreach_set_typing"
    recipient: str = Field(min_length=1)
