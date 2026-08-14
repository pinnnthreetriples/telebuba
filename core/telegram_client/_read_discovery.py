"""Read-only channel-discovery dispatchers — Telegram's own channel search.

Extracted-sibling pattern (see ``_read_channels.py``): ``_read.py`` keeps the
match and imports these dispatchers. Errors ride the ``execute_read_many``
ladder untouched (RPC → ``TelegramReadError``).

Every dispatcher keeps only entries that are broadcast channels **with a public
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
from telethon.tl.functions.messages import SearchGlobalRequest
from telethon.tl.types import InputMessagesFilterEmpty, InputPeerEmpty

from core.telegram_client._channels import ChannelGatewayError
from schemas.telegram_actions_discovery import (
    CHANNEL_SEARCH_MIN_QUERY_LENGTH,
    GlobalPostsCursor,
    TelegramChannelMatch,
    TelegramChannelMatches,
    TelegramGlobalPostMatches,
)

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions import GetSimilarChannels, SearchChannels, SearchGlobalPosts


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
    against the account's own subscriptions. A seed Telegram cannot resolve is a
    refusal, not an empty answer: swallowing it reported the source as having run
    and found nothing, which is exactly what a perfectly good seed with no
    recommendations looks like — so the operator kept a dead handle in the form.
    """
    seed = None if action.seed is None else action.seed.strip().lstrip("@")
    channel: object = None
    if seed:
        try:
            channel = await client.get_input_entity(seed)
        except (ValueError, TypeError) as exc:
            # Unknown/invalid handle: Telethon raises ValueError for an unresolvable
            # peer. The same stable code the write side uses, so it rides the
            # ``execute_read_many`` ladder as a ``TelegramReadError`` like any refusal.
            code = "channel_not_found"
            raise ChannelGatewayError(code) from exc
    result = await client(GetChannelRecommendationsRequest(channel=channel))  # ty: ignore[invalid-argument-type]
    return _collect(getattr(result, "chats", None))


async def dispatch_search_global_posts(
    client: TelegramClient,
    action: SearchGlobalPosts,
) -> TelegramGlobalPostMatches:
    """Channels that POSTED a match (``messages.searchGlobal``, broadcasts only).

    A different index from ``contacts.search``: it reads message content, so it
    surfaces channels whose title never carries the keyword. Its only documented
    query error is ``SEARCH_QUERY_EMPTY`` — there is no four-character minimum
    here — so the local short-circuit only covers a blank query.

    The reply's ``chats`` vector already holds each matching channel exactly once,
    however many of its posts matched, so it *is* the de-duplicated page; the
    channels are read from it rather than re-derived per message.

    Paging is Telegram's three-value offset, carried back in ``next_cursor``. It
    is absent only when the page held no message to continue from, so a caller
    bounds its own page count instead of waiting for the search to say "done".
    """
    query = action.query.strip()
    if not query:
        return TelegramGlobalPostMatches(items=[])
    cursor = action.cursor or GlobalPostsCursor()
    offset_peer: object = InputPeerEmpty()
    if cursor.peer:
        try:
            offset_peer = await client.get_input_entity(cursor.peer)
        except (ValueError, TypeError):
            # Handle gone since the previous page: page on from the rate/id alone,
            # which is what Telethon's own global-search iterator falls back to.
            offset_peer = InputPeerEmpty()
    result = await client(
        SearchGlobalRequest(
            q=query,
            filter=InputMessagesFilterEmpty(),
            min_date=None,
            max_date=None,
            offset_rate=cursor.offset_rate,
            offset_peer=offset_peer,
            offset_id=cursor.offset_id,
            limit=action.limit,
            broadcasts_only=True,
        ),
    )
    chats = getattr(result, "chats", None)
    return TelegramGlobalPostMatches(
        items=_collect(chats).items,
        next_cursor=_next_cursor(result, chats),
    )


def _next_cursor(result: object, chats: object) -> GlobalPostsCursor | None:
    messages = getattr(result, "messages", None)
    if not isinstance(messages, list) or not messages:
        return None
    last = messages[-1]
    return GlobalPostsCursor(
        offset_rate=_offset_rate(result, last),
        peer=_peer_handle(last, chats),
        offset_id=int(getattr(last, "id", 0) or 0),
    )


def _offset_rate(result: object, last_message: object) -> int:
    """``next_rate``, or — as the method documents — the last message's date."""
    rate = getattr(result, "next_rate", None)
    if isinstance(rate, int):
        return rate
    stamp = getattr(getattr(last_message, "date", None), "timestamp", None)
    seconds = stamp() if callable(stamp) else 0
    return int(seconds) if isinstance(seconds, int | float) else 0


def _peer_handle(message: object, chats: object) -> str | None:
    """The handle of the channel that posted ``message``, matched out of ``chats``."""
    channel_id = getattr(getattr(message, "peer_id", None), "channel_id", None)
    if not isinstance(channel_id, int) or not isinstance(chats, list):
        return None
    for chat in chats:
        if getattr(chat, "id", None) != channel_id:
            continue
        username = getattr(chat, "username", None)
        return username.strip() if isinstance(username, str) and username.strip() else None
    return None
