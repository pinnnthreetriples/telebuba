"""Warming reads that browse content the way an opened app does.

The chat list, an in-channel search, a link preview, the sticker and GIF panels, and
one query to an official inline bot. Every request is a read; the log rows carry
counts and lengths only — never a word, a URL or a result the account saw.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from telethon import errors
from telethon.helpers import add_surrogate, del_surrogate
from telethon.tl.functions.channels import GetMessagesRequest
from telethon.tl.functions.messages import (
    GetAllStickersRequest,
    GetDialogsRequest,
    GetFeaturedStickersRequest,
    GetInlineBotResultsRequest,
    GetRecentStickersRequest,
    GetSavedGifsRequest,
    GetStickerSetRequest,
    GetWebPageRequest,
    SearchGlobalRequest,
    SearchRequest,
)
from telethon.tl.types import (
    InputMessageID,
    InputMessagesFilterEmpty,
    InputPeerEmpty,
    InputPeerSelf,
    InputStickerSetID,
    MessageEntityTextUrl,
    MessageEntityUrl,
    MessageMediaWebPage,
)

from core.telegram_client._action_results import _DispatchResult
from schemas.telegram_actions_warming import INLINE_BOT_WHITELIST

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from telethon import TelegramClient
    from telethon.tl.types import TypeInputPeer

    from schemas.telegram_actions import (
        WarmGetDialogs,
        WarmInlineQuery,
        WarmLinkPreview,
        WarmSearchMessages,
    )

_MIN_WORD_LETTERS = 4
_SEARCH_LIMIT = 20
_WORD = re.compile(r"\w+")
# Opening an invite link is a join hint to Telegram, not a preview.
_INVITE_MARKERS = ("t.me/+", "t.me/joinchat", "/joinchat/")


async def get_dialogs(client: TelegramClient, action: WarmGetDialogs) -> _DispatchResult:
    """Open the chat list from the top; only the count is kept, never the peers."""
    result = await client(
        GetDialogsRequest(
            offset_date=None,
            offset_id=0,
            offset_peer=InputPeerEmpty(),
            limit=action.limit,
            hash=0,
        ),
    )
    # ``DialogsNotModified`` carries no list — it reads as zero dialogs.
    return _DispatchResult(log_extra={"dialogs": len(getattr(result, "dialogs", ()))})


async def _fetch_channel_messages(
    client: TelegramClient,
    channel: str,
    ids: list[int],
) -> tuple[object, list[object]]:
    """The dispatcher's own first read: the posts a following request draws from."""
    peer = await client.get_input_entity(channel)
    result = await client(
        GetMessagesRequest(channel=peer, id=[InputMessageID(i) for i in ids]),  # ty: ignore[invalid-argument-type]
    )
    # A ``MessageEmpty`` slot needs no filter: its text, entities and media read as None.
    return peer, list(getattr(result, "messages", ()))


def _pick_word(messages: Sequence[object]) -> str | None:
    """First alphabetic word of four or more letters, in post order, any script."""
    for message in messages:
        for word in _WORD.findall(getattr(message, "message", None) or ""):
            if len(word) >= _MIN_WORD_LETTERS and word.isalpha():
                return word
    return None


def _message_urls(message: object) -> Iterator[str]:
    # Entity offsets count UTF-16 units, so slice the surrogate form as Telethon does.
    text = add_surrogate(getattr(message, "message", None) or "")
    for entity in getattr(message, "entities", None) or ():
        if isinstance(entity, MessageEntityUrl):
            yield del_surrogate(text[entity.offset : entity.offset + entity.length])
        elif isinstance(entity, MessageEntityTextUrl):
            yield entity.url
    media = getattr(message, "media", None)
    if isinstance(media, MessageMediaWebPage):
        url = getattr(media.webpage, "url", None)
        if url:
            yield url


def _first_url(messages: Sequence[object]) -> str | None:
    """First http(s) link — entity, text link or attached preview — that is not an invite."""
    for message in messages:
        for url in _message_urls(message):
            if url.startswith(("http://", "https://")) and not any(
                marker in url for marker in _INVITE_MARKERS
            ):
                return url
    return None


async def search_messages(client: TelegramClient, action: WarmSearchMessages) -> _DispatchResult:
    """Search the channel for a word from a post just read; ``global_search`` adds one global."""
    peer, messages = await _fetch_channel_messages(client, action.channel, action.message_ids)
    word = _pick_word(messages)
    query = action.fallback_query if word is None else word
    result = await client(
        SearchRequest(
            peer=peer,  # ty: ignore[invalid-argument-type]
            q=query,
            filter=InputMessagesFilterEmpty(),
            min_date=None,
            max_date=None,
            offset_id=0,
            add_offset=0,
            limit=_SEARCH_LIMIT,
            max_id=0,
            min_id=0,
            hash=0,
        ),
    )
    # ``MessagesNotModified`` carries no list — it reads as zero matches.
    found = len(getattr(result, "messages", ()))
    if action.global_search:
        result = await client(
            SearchGlobalRequest(
                q=query,
                filter=InputMessagesFilterEmpty(),
                min_date=None,
                max_date=None,
                offset_rate=0,
                offset_peer=InputPeerEmpty(),
                offset_id=0,
                limit=_SEARCH_LIMIT,
            ),
        )
        found += len(getattr(result, "messages", ()))
    return _DispatchResult(
        log_extra={"query_len": len(query), "found": found, "fallback": word is None},
    )


async def link_preview(client: TelegramClient, action: WarmLinkPreview) -> _DispatchResult:
    """Open the preview of the first http(s) link in a post just read."""
    _, messages = await _fetch_channel_messages(client, action.channel, action.message_ids)
    url = _first_url(messages)
    if url is None:
        return _DispatchResult(log_extra={"warm_skip": "no_url"})
    try:
        result = await client(GetWebPageRequest(url=url, hash=0))
    except (
        errors.WebpageCurlFailedError,
        errors.WebpageMediaEmptyError,
        errors.UrlInvalidError,
    ):
        return _DispatchResult(log_extra={"warm_skip": "webpage_unavailable"})
    # ``WebPagePending`` is a normal answer: Telegram is still fetching the page.
    webpage = result.webpage
    return _DispatchResult(log_extra={"webpage": type(webpage).__name__})


async def browse_stickers(client: TelegramClient) -> _DispatchResult:
    """Open the sticker panel — installed, recent, featured — and peek at one featured set.

    Never installs anything: the set is only viewed, as the panel preview does.
    """
    installed = await client(GetAllStickersRequest(hash=0))
    await client(GetRecentStickersRequest(hash=0))
    featured = await client(GetFeaturedStickersRequest(hash=0))
    sets = getattr(featured, "sets", ())
    # ``*NotModified`` answers carry no list — they read as zero.
    log_extra: dict[str, object] = {
        "installed": len(getattr(installed, "sets", ())),
        "featured": len(sets),
        "set_documents": 0,
    }
    if sets:
        chosen = sets[0].set
        try:
            full = await client(
                GetStickerSetRequest(
                    stickerset=InputStickerSetID(id=chosen.id, access_hash=chosen.access_hash),
                    hash=0,
                ),
            )
        except errors.StickersetInvalidError:
            log_extra["warm_skip"] = "set_gone"
        else:
            log_extra["set_documents"] = len(getattr(full, "documents", ()))
    return _DispatchResult(log_extra=log_extra)


async def saved_gifs(client: TelegramClient) -> _DispatchResult:
    """Open the GIF tab; never saves one."""
    result = await client(GetSavedGifsRequest(hash=0))
    # ``SavedGifsNotModified`` carries no list — it reads as zero.
    return _DispatchResult(log_extra={"saved_gifs": len(getattr(result, "gifs", ()))})


async def resolve_inline_bot(client: TelegramClient, username: str | None) -> TypeInputPeer:
    """Resolve one whitelisted official bot; anything else is refused before any RPC.

    An arbitrary bot is a third party that would see the query — hence the list. The
    session entity cache answers repeat draws without a ``contacts.resolveUsername``.
    """
    if username not in INLINE_BOT_WHITELIST:
        msg = "warming may only address a whitelisted inline bot"
        raise ValueError(msg)
    return await client.get_input_entity(username)


async def inline_query(client: TelegramClient, action: WarmInlineQuery) -> _DispatchResult:
    """Ask an inline bot for results, as typing ``@bot query`` does; nothing is ever sent."""
    bot = await resolve_inline_bot(client, action.bot)
    try:
        results = await client(
            GetInlineBotResultsRequest(
                bot=bot,  # ty: ignore[invalid-argument-type]
                peer=InputPeerSelf(),
                query=action.query,
                offset="",
                geo_point=None,
            ),
        )
    # ``errors.TimeoutError`` is Telethon's 503 "bot did not answer", not the builtin.
    except (errors.BotResponseTimeoutError, errors.TimeoutError):
        return _DispatchResult(log_extra={"warm_skip": "bot_timeout"})
    except errors.BotInlineDisabledError:
        return _DispatchResult(log_extra={"warm_skip": "bot_invalid"})
    return _DispatchResult(
        log_extra={"results": len(getattr(results, "results", ())), "query_len": len(action.query)},
    )
