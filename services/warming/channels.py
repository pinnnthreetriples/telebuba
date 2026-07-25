"""Warming channel management — parse free-form input, persist unique channels.

UI-facing list/add/remove that delegate persistence to ``core.db``. No Telegram
I/O happens here; joining channels is part of the warming cycle in the engine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.channel_tokens import dedup_key as _dedup_key
from core.channel_tokens import normalize_channel, parse_channels
from core.config import settings
from core.db import add_warming_channel, list_warming_channels, remove_warming_channel
from core.logging import log_event

if TYPE_CHECKING:
    from schemas.warming import AddChannelsRequest, RemoveChannelRequest, WarmingChannelList


# Token parsing itself lives in core.channel_tokens (shared with neurocomment
# discovery); these thin wrappers bind warming's own length policy.
def _normalize_channel(token: str) -> str | None:
    return normalize_channel(token, max_length=settings.warming.max_channel_length)


def _parse_channels(raw: str) -> list[str]:
    return parse_channels(raw, max_length=settings.warming.max_channel_length)


async def list_channels() -> WarmingChannelList:
    return await list_warming_channels()


async def add_channels(data: AddChannelsRequest) -> WarmingChannelList:
    """Parse a free-form blob of links/usernames and persist each unique one.

    Enforces ``settings.warming.max_channels_per_add`` and
    ``settings.warming.max_channels_total`` — junk uploads cannot grow the table
    without bound.
    """
    parsed = _parse_channels(data.raw)
    if not parsed:
        return await list_warming_channels()

    warm = settings.warming
    parsed = parsed[: warm.max_channels_per_add]
    existing = await list_warming_channels()
    existing_keys = {_dedup_key(ch.channel) for ch in existing.channels}
    headroom = max(0, warm.max_channels_total - len(existing_keys))

    channels = existing
    added = 0
    for channel in parsed:
        if added >= headroom:
            await log_event(
                "WARNING",
                "warming_channel_limit_reached",
                extra={"limit": warm.max_channels_total},
            )
            break
        if _dedup_key(channel) in existing_keys:
            continue
        channels = await add_warming_channel(channel)
        existing_keys.add(_dedup_key(channel))
        added += 1
    await log_event(
        "INFO",
        "warming_channels_added",
        extra={"count": added, "submitted": len(parsed)},
    )
    return channels


async def remove_channel(data: RemoveChannelRequest) -> WarmingChannelList:
    channels = await remove_warming_channel(data.channel)
    await log_event("INFO", "warming_channel_removed", extra={"channel": data.channel})
    return channels
