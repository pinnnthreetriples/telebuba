"""``read_channel`` — fetch a channel's recent posts and mark them read.

Split from ``_actions.py`` (aislop file-size budget). The read already holds the
``Message`` objects, so it also reports which posts carry a poll / video / voice a
warming extra can act on: the extras step then aims at a post that has one instead of
drawing a random channel and paying a budgeted attempt to find nothing there.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.telegram_client._action_results import _DispatchResult
from core.telegram_client._warm_media import media_kind
from schemas.telegram_action_results import ReadChannelFacts

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions import ReadChannel


async def dispatch_read_channel(client: TelegramClient, action: ReadChannel) -> _DispatchResult:
    """Fetch recent posts and mark them read — the "reading a feed" emulation.

    Returns the ids fetched so a following react on the same channel reuses them
    instead of issuing a second identical ``get_messages``.
    """
    messages = await client.get_messages(action.channel, limit=action.message_limit)
    # get_messages(limit=...) returns an iterable TotalList; the stub union also
    # admits a single Message / None for the by-id form, which we never use here.
    posts = [m for m in messages if getattr(m, "id", None)]  # ty: ignore[not-iterable]
    ids = [int(getattr(m, "id", 0)) for m in posts]
    max_id = max(ids, default=0)
    if max_id:
        await client.send_read_acknowledge(action.channel, max_id=max_id)
    kinds = {int(getattr(m, "id", 0)): kind for m in posts if (kind := media_kind(m)) is not None}
    return _DispatchResult(
        recent_message_ids=ids,
        recent_media_kinds=kinds,
        read_facts=_read_facts(posts),
    )


# Enough prose for a stop-word vote; the rest of a long feed only costs memory.
_TEXT_SAMPLE_CHARS = 4000


def _read_facts(posts: list[object]) -> ReadChannelFacts | None:
    """What the fetched page says about the channel itself, at no extra RPC.

    ``post`` is set on every broadcast-channel message and on no group message, so it
    names the kind; the newest date is when the channel last published. Only real
    strings and datetimes count, so a stub message never fakes a label.
    """
    if not posts:
        return None
    texts = [text for m in posts if isinstance(text := getattr(m, "message", None), str)]
    dates = [date for m in posts if isinstance(date := getattr(m, "date", None), datetime)]
    return ReadChannelFacts(
        text_sample=" ".join(texts)[:_TEXT_SAMPLE_CHARS],
        kind="channel" if any(getattr(m, "post", None) is True for m in posts) else "group",
        last_post_at=max(dates).astimezone(UTC).isoformat() if dates else None,
    )
