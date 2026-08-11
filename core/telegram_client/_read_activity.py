"""Channel-liveness dispatcher — when did this channel last publish anything?

Extracted-sibling pattern (see ``_read_channels.py``): ``_read.py`` keeps the match and
imports the dispatcher. Its own module rather than a case in that one, which is the
OWNED-channel cluster and resolves everything through ``_input_channel``; this read takes
the campaign's handle string and lets Telethon resolve it like every other neurocomment
read does.
"""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING

from schemas.telegram_actions_activity import LastPostResult

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions_activity import GetLastPostAt


async def dispatch_get_last_post_at(
    client: TelegramClient,
    action: GetLastPostAt,
) -> LastPostResult:
    """The newest message's date, normalised to UTC; ``None`` for an empty channel.

    ``limit=1`` on purpose: the caller only ever compares this against one cutoff, so
    reading a page would cost the same RPC and hand it fourteen dates it has no use for.

    A message with no usable date reads as an empty channel rather than as an error —
    Telethon always sets one, so the guard is for a stub or a truncated update, and in
    both cases the honest answer is "nothing datable here", which the caller then
    verifies against its own records before acting.
    """
    messages = await client.get_messages(action.channel, limit=1)
    for message in messages:  # ty: ignore[not-iterable]
        date = getattr(message, "date", None)
        if date is not None:
            return LastPostResult(last_post_at=date.astimezone(UTC).isoformat())
    return LastPostResult()
