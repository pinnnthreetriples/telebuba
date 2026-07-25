"""Read-only channel-discovery dispatchers — Telegram's own channel search.

Extracted-sibling pattern (see ``_read_channels.py``): ``_read.py`` keeps the
match and imports these dispatchers. Errors ride the ``execute_read_many``
ladder untouched (RPC → ``TelegramReadError``).

Both dispatchers keep only entries that are broadcast channels **with a public
username**: a campaign channel is stored as a handle, so a private or
username-less result could never be linked, and the ``broadcast`` check drops the
discussion supergroups that share the same result vector.

Every Telethon attribute is read through ``getattr(..., default)``, matching the
rest of the gateway — a layer change upstream (or a MagicMock in a test) then
yields an empty field instead of raising.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telethon.tl.functions.channels import GetChannelRecommendationsRequest
from telethon.tl.functions.contacts import SearchRequest

from schemas.telegram_actions_discovery import (
    CHANNEL_SEARCH_MIN_QUERY_LENGTH,
    TelegramChannelMatch,
    TelegramChannelMatches,
)

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions import GetSimilarChannels, SearchChannels


def _to_match(entity: object) -> TelegramChannelMatch | None:
    if not getattr(entity, "broadcast", False):
        return None
    username = getattr(entity, "username", None)
    if not isinstance(username, str) or not username.strip():
        return None
    participants = getattr(entity, "participants_count", None)
    return TelegramChannelMatch(
        username=username.strip(),
        title=str(getattr(entity, "title", "") or ""),
        participants_count=participants if isinstance(participants, int) else None,
    )


def _collect(chats: object) -> TelegramChannelMatches:
    if not isinstance(chats, list):
        return TelegramChannelMatches(items=[])
    matches = (_to_match(chat) for chat in chats)
    return TelegramChannelMatches(items=[match for match in matches if match is not None])


async def dispatch_search_channels(
    client: TelegramClient,
    action: SearchChannels,
) -> TelegramChannelMatches:
    """Public broadcast channels matching a keyword (``contacts.search``).

    The server decides how many results to return and offers no offset, so breadth
    comes from varying the query rather than paging. Queries shorter than
    Telegram's own minimum are answered locally: the RPC would only ever return
    ``QUERY_TOO_SHORT``, and spending flood budget on a guaranteed error is worse
    than an empty result.
    """
    query = action.query.strip()
    if len(query) < CHANNEL_SEARCH_MIN_QUERY_LENGTH:
        return TelegramChannelMatches(items=[])
    result = await client(SearchRequest(q=query, limit=action.limit, broadcasts=True))
    return _collect(getattr(result, "chats", None))


async def dispatch_get_similar_channels(
    client: TelegramClient,
    action: GetSimilarChannels,
) -> TelegramChannelMatches:
    """Channels similar to a seed (``channels.getChannelRecommendations``).

    Free of the search-flood exposure that repeated keyword queries carry, which
    makes it the cheapest way to widen a sweep. With no seed, Telegram recommends
    against the account's own subscriptions. A seed we cannot resolve yields an
    empty result rather than failing the run — the keyword arm still has results.
    """
    seed = None if action.seed is None else action.seed.strip().lstrip("@")
    channel: object = None
    if seed:
        try:
            channel = await client.get_input_entity(seed)
        except (ValueError, TypeError):
            # Unknown/invalid handle: Telethon raises ValueError for an unresolvable
            # peer. RPC-level failures still ride the execute_read_many ladder.
            return TelegramChannelMatches(items=[])
    result = await client(GetChannelRecommendationsRequest(channel=channel))  # ty: ignore[invalid-argument-type]
    return _collect(getattr(result, "chats", None))
