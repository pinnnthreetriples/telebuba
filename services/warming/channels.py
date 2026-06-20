"""Warming channel management — parse free-form input, persist unique channels.

UI-facing list/add/remove that delegate persistence to ``core.db``. No Telegram
I/O happens here; joining channels is part of the warming cycle in the engine.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from core.config import settings
from core.db import add_warming_channel, list_warming_channels, remove_warming_channel
from core.logging import log_event
from core.telegram_client._util import extract_invite_hash

if TYPE_CHECKING:
    from schemas.warming import AddChannelsRequest, RemoveChannelRequest, WarmingChannelList

# Allowed token format for a Telegram public channel/group identifier.
# Joinchat/invite links are intercepted earlier by extract_invite_hash.
_CHANNEL_TOKEN_RE = re.compile(r"^@?[A-Za-z0-9_]{3,32}$")


def _normalize_channel(token: str) -> str | None:  # noqa: PLR0911
    invite_hash = extract_invite_hash(token)
    if invite_hash:
        return f"+{invite_hash}"

    cleaned = token.strip().strip("<>").rstrip("/")
    if not cleaned:
        return None

    # Strip query parameters (like ?single)
    cleaned = cleaned.split("?")[0]

    lowered = cleaned.lower()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/", "telegram.me/"):
        if lowered.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    else:
        # Reject bare tokens that contain a slash (e.g. channel/123)
        if "/" in cleaned:
            return None

    cleaned = cleaned.lstrip("@")
    if not cleaned:
        return None
    # Reject private chat links (e.g. t.me/c/12345/1)
    if cleaned.lower().startswith("c/"):
        return None

    # If it was a valid public post link (e.g. t.me/mychannel/123), extract the channel
    if "/" in cleaned:
        cleaned = cleaned.split("/")[0]
    if len(cleaned) > settings.warming.max_channel_length:
        return None
    return cleaned if _CHANNEL_TOKEN_RE.match(cleaned) else None


def _dedup_key(channel: str) -> str:
    """Case-folding key for dedup.

    Public usernames are case-insensitive, but private-invite hashes ("+HASH")
    are case-sensitive — two genuinely different invites that differ only in
    letter case must not collapse to one and silently drop the second.
    """
    return channel if channel.startswith("+") else channel.lower()


def _parse_channels(raw: str) -> list[str]:
    seen: list[str] = []
    seen_keys: set[str] = set()
    for token in re.split(r"[\s,]+", raw.strip()):
        normalized = _normalize_channel(token)
        if normalized is None:
            continue
        key = _dedup_key(normalized)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        seen.append(normalized)
    return seen


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
