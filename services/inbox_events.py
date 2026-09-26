"""Layer-legal adapter from Telegram updates to the transient inbox event bus."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.inbox_events import publish

if TYPE_CHECKING:
    from schemas.inbox_events import InboxMessageReceived


async def on_message_received(event: InboxMessageReceived) -> None:
    """Publish a typed message nudge; message content is fetched through HTTP."""
    publish(event)
