"""Push listener — a dedicated account watches channels and surfaces new posts.

Unlike the request/response gateway (``_actions`` / ``_read``), this is a
*standing* subscription: one Telethon ``NewMessage`` handler stays registered on
the pooled client and fires for each fresh broadcast post. We translate the
Telethon event into a typed :class:`NewPostEvent` and hand it to a caller-injected
async callback, so no Telethon object leaks above ``core/``.

Only NEW posts: ``events.NewMessage`` fires for messages arriving after
registration while the loop runs — we never call ``client.catch_up()`` (the only
thing that would replay history). Telethon reconnects automatically and the
in-memory handler survives the reconnect; after a full process restart the caller
re-invokes :func:`subscribe_posts` (idempotent) to re-establish it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from telethon import events
from telethon.errors import (
    ChannelPrivateError,
    UserBannedInChannelError,
    UserNotParticipantError,
)
from telethon.tl.types import MessageMediaPhoto

from core.logging import log_event
from core.telegram_client._pool import _CLIENTS, get_client, register_rebuild_hook
from schemas.telegram_actions import NewPostEvent

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from telethon import TelegramClient

    from schemas.telegram_actions import PostMediaKind

    _EventHandler = Callable[[events.NewMessage.Event], Awaitable[None]]

# Stdlib sink for full third-party text — see ``core.proxy_check._failed_result``.
logger = logging.getLogger(__name__)

__all__ = [
    "fetch_recent_posts",
    "forget_post_listener",
    "stop_post_listener",
    "subscribe_posts",
    "take_lost_access_channels",
    "update_post_subscription",
]


# ponytail: single-process registry. One app instance owns the listener account;
# a multi-process deployment would need this state in shared storage instead.
_HANDLERS: dict[str, _EventHandler] = {}
# The channel-filter each handler was registered with, so a pool rebuild can
# re-attach the same subscription on the replacement client.
_FILTERS: dict[str, object] = {}
# One lock + monotonically increasing generation per account make subscribe, stop and
# pool-rebuild reattach linearizable. Without them a slow subscribe could register after
# a later stop, or an old callback could keep delivering after a subscription switch.
_SUBSCRIPTION_LOCKS: dict[str, asyncio.Lock] = {}
_GENERATIONS: dict[str, int] = {}
# Generations are drawn from ONE process-wide sequence rather than counted per account,
# so a number is never handed out twice. That is what makes ``forget_post_listener``
# safe: an account id can be deleted and re-created, and a subscribe pass still in
# flight from the previous incarnation cannot match the new one's first generation.
_GENERATION_SEQUENCE = 0
# Resolved peer id per channel, so reconcile does not re-issue a get_peer_id RPC
# for every watched channel on every call (fires on each link/unlink + boot =
# O(channels) serial RPCs otherwise). Peer ids are stable, so a stale entry is
# harmless. ponytail: process-lifetime, never invalidated — only successful
# resolutions are cached, so a failure simply retries on the next reconcile.
_PEER_IDS: dict[str, int] = {}
# Telethon errors that mean the account is not inside a channel it is trying to resolve.
# The classes themselves, not their names: the set used to hold strings compared against
# ``type(exc).__name__``, so a rename upstream or one wrong guess here would simply stop
# matching, in silence. ``ChannelPrivateError`` is the one ``get_peer_id`` actually raises;
# the other two are kept because the same verdict reaches this module by other routes
# (``GetParticipant`` and message sending respectively) and cost nothing to name.
_LOST_ACCESS_ERRORS = (ChannelPrivateError, UserNotParticipantError, UserBannedInChannelError)
# Channels each account is known to be out of, drained by the caller. This layer only
# witnesses the loss; what to do about it (stamp the join-log row, re-join, give up) is the
# join pass's policy. ponytail: single-process, in-memory, like ``_PEER_IDS`` above.
#
# NARROW, deliberately stated: the only witness is a FAILED ``get_peer_id`` below, which
# runs at subscribe time and only for a channel with no cached peer id. So a kick is noticed
# on the next reconcile that has to re-resolve — after a restart, or for a channel that
# never resolved — and NOT while the process runs. A public channel is not covered at all:
# its ``@username`` keeps resolving to the same peer after a kick, so the loss shows up as
# silence, not as an error. Closing that needs a runtime signal (an update handler for
# ``channelParticipantLeft``/``ChannelForbidden``), which this slice does not add.
_LOST_ACCESS: dict[str, set[str]] = {}


def _subscription_lock(account_id: str) -> asyncio.Lock:
    lock = _SUBSCRIPTION_LOCKS.get(account_id)
    if lock is None:
        lock = _SUBSCRIPTION_LOCKS[account_id] = asyncio.Lock()
    return lock


def _next_generation(account_id: str) -> int:
    global _GENERATION_SEQUENCE  # noqa: PLW0603 - single-process registry, like the dicts above
    _GENERATION_SEQUENCE += 1
    _GENERATIONS[account_id] = _GENERATION_SEQUENCE
    return _GENERATION_SEQUENCE


def take_lost_access_channels(account_id: str) -> set[str]:
    """Channels ``account_id`` failed to resolve as a member since the last call (drains).

    Not "every channel it was kicked from": see ``_LOST_ACCESS`` for what this can and
    cannot see.
    """
    return _LOST_ACCESS.pop(account_id, set())


async def _reattach_on_rebuild(account_id: str, client: TelegramClient) -> None:
    """Pool rebuild hook: re-register this account's handler on the new client.

    The pool builds a fresh, handler-less client whenever the cached one drops
    its connection. Without this the standing subscription would silently stop
    firing until the next boot-time reconcile. Keyed off ``_HANDLERS`` so a
    stopped listener (already popped) is never resurrected.
    """
    async with _subscription_lock(account_id):
        handler = _HANDLERS.get(account_id)
        event_filter = _FILTERS.get(account_id)
        if handler is None or event_filter is None:
            return
        # ``_FILTERS`` stores the ``events.NewMessage`` instance as ``object``; the
        # stub types the param as ``EventBuilder``.
        client.add_event_handler(handler, event_filter)  # ty: ignore[invalid-argument-type]


register_rebuild_hook(_reattach_on_rebuild)


async def subscribe_posts(  # noqa: C901, PLR0911 - generation-fenced I/O state machine
    account_id: str,
    channels: list[str],
    on_post: Callable[[NewPostEvent], Awaitable[None]],
) -> list[str]:
    """Register a single ``NewMessage`` handler watching ``channels`` for new posts.

    Idempotent: re-subscribing for the same account first removes the prior
    handler, so it is safe to call on every app start. ``channels`` is the
    whitelist — only those channels fire. Each fresh broadcast post is mapped
    back to its original subscription string and pushed to ``on_post``; a
    callback error is logged and swallowed so it can't kill the listener.

    Returns the channels actually registered, in the caller's own handle form (the
    strings the rest of the system keys on). A channel we cannot resolve to a peer
    id is left out of the filter, so no post from it will EVER arrive — the caller
    only learns about that gap from this return value.
    """
    # Reserve ownership before connecting. Pool construction calls rebuild hooks, so
    # holding this lock across ``get_client`` would deadlock when our own hook re-enters.
    # A stop/new subscribe during the connect advances the generation and wins.
    async with _subscription_lock(account_id):
        generation = _next_generation(account_id)
        _detach_locked(account_id)
    client = await get_client(account_id)

    # Peer resolution can block on Telegram. It must never hold the subscription lock:
    # Stop/delete bump the generation and return immediately while this stale pass winds
    # down. Every await is followed by an ownership check before another RPC or publish.
    channel_by_peer_id: dict[int, str] = {}
    resolved: list[str] = []
    for channel in channels:
        if _GENERATIONS.get(account_id) != generation:
            return []
        peer_id = _PEER_IDS.get(channel)
        if peer_id is None:
            try:
                peer_id = await client.get_peer_id(channel)
            except Exception as exc:  # noqa: BLE001 - isolate one bad channel
                if _GENERATIONS.get(account_id) != generation:
                    return []
                # ponytail: transport code named after its caller's domain. The log
                # feeds are separated only by event-name prefix, and neurocomment is
                # today the sole consumer of the post listener.
                if isinstance(exc, _LOST_ACCESS_ERRORS):
                    _LOST_ACCESS.setdefault(account_id, set()).add(channel)
                await log_event(
                    "WARNING",
                    "neurocomment_listener_channel_unresolved",
                    account_id=account_id,
                    extra={"channel": channel, "error_type": type(exc).__name__},
                )
                if _GENERATIONS.get(account_id) != generation:
                    return []
                continue
            if _GENERATIONS.get(account_id) != generation:
                return []
            _PEER_IDS[channel] = peer_id
        channel_by_peer_id[peer_id] = channel
        resolved.append(channel)

    if not resolved:
        # Nothing resolved → do NOT register: events.NewMessage(chats=[]) would
        # watch EVERY chat. An empty whitelist must mean "listen to nothing".
        return resolved

    # Await-free commit: Stop either wins before this lock (generation mismatch) or
    # immediately after registration and detaches the exact handler we publish here.
    async with _subscription_lock(account_id):
        if _GENERATIONS.get(account_id) != generation:
            return []
        handler = _make_handler(
            account_id,
            channel_by_peer_id,
            on_post,
            generation=generation,
        )
        event_filter = events.NewMessage(chats=resolved)
        client.add_event_handler(handler, event_filter)
        _HANDLERS[account_id] = handler
        _FILTERS[account_id] = event_filter
        return resolved


async def update_post_subscription(
    account_id: str,
    channels: list[str],
    on_post: Callable[[NewPostEvent], Awaitable[None]],
) -> list[str]:
    """Swap the watched set: drop the old handler, register the new one; return what is watched."""
    return await subscribe_posts(account_id, channels, on_post)


async def stop_post_listener(account_id: str) -> None:
    """Remove the account's handler and clear its state; no-op if none.

    The handler is popped from ``_HANDLERS`` first, so a concurrent pool
    rebuild (whose hook reads ``_HANDLERS``) can no longer re-add it. We only
    detach from an *already cached* client — never call ``get_client``, which
    would force a fresh connect just to drop a handler (and could raise while
    the pool is shutting down). If nothing is cached, dropping the registry
    entry is enough: a future rebuild won't carry the handler.
    """
    async with _subscription_lock(account_id):
        _next_generation(account_id)
        _detach_locked(account_id)


async def forget_post_listener(account_id: str) -> None:
    """Stop the subscription of a DELETED account and drop its registry entries.

    ``stop_post_listener`` deliberately keeps the generation and the lock: the account
    still exists, so a later Start has to be able to out-number a pass that is still
    winding down. A deleted account gets no later Start, and its two per-account
    entries would otherwise sit in memory for the life of the process — one pair per
    account ever deleted.

    Dropping the generation is not a weakening: a stale pass compares against ``None``
    and loses, and the sequence it drew from never repeats a number.
    """
    async with _subscription_lock(account_id):
        _next_generation(account_id)
        _detach_locked(account_id)
        _GENERATIONS.pop(account_id, None)
    # Popped after release, never while held. A deleted account has no later caller to
    # contend with, so no one can end up waiting on a lock a newcomer would not find.
    _SUBSCRIPTION_LOCKS.pop(account_id, None)


def _detach_locked(account_id: str) -> None:
    """Detach one subscription while its account lock is held."""
    handler = _HANDLERS.pop(account_id, None)
    _FILTERS.pop(account_id, None)
    # The gap report goes with the subscription that witnessed it: this is also the account
    # switch and shutdown path, so a previous listener's undrained losses must not be handed
    # to whatever runs next. Dropped before the early return, which only skips the detach.
    _LOST_ACCESS.pop(account_id, None)
    if handler is None:
        return
    client = _CLIENTS.get(account_id)
    if client is None:
        return
    # Telethon accepts the EventBuilder *class* here to drop all NewMessage
    # handlers; its stub only types the instance form.
    client.remove_event_handler(handler, events.NewMessage)  # ty: ignore[invalid-argument-type]


async def fetch_recent_posts(
    account_id: str,
    channel: str,
    *,
    limit: int,
    before_post_id: int | None = None,
) -> list[NewPostEvent]:
    """Read a small newest-first history page for explicit, bounded gap recovery.

    This is deliberately not Telethon ``catch_up``: callers choose one known channel,
    a hard row limit and a freshness cutoff. The push handler is already installed when
    this runs, and the durable inbox deduplicates overlap between the two sources.
    """
    client = await get_client(account_id)
    messages = await client.get_messages(
        channel,
        limit=limit,
        offset_id=before_post_id or 0,
    )
    result: list[NewPostEvent] = []
    for message in messages:  # ty: ignore[not-iterable]
        if getattr(message, "post", None) is not True:
            continue
        raw_date = getattr(message, "date", None)
        date_unix = int(raw_date.timestamp()) if isinstance(raw_date, datetime) else 0
        result.append(
            NewPostEvent(
                channel=channel,
                post_id=int(getattr(message, "id", 0) or 0),
                text=str(getattr(message, "message", "") or ""),
                media_kind=_media_kind(message),
                is_forward=getattr(message, "fwd_from", None) is not None,
                date_unix=max(0, date_unix),
            ),
        )
    return [event for event in result if event.post_id > 0]


def _media_kind(message: object) -> PostMediaKind:
    """Classify the post's media by what a comment could be made OUT of.

    A standalone photo is the only kind the engine can still comment on when the post
    carries no caption (it downloads it and lets the model look). The album check comes
    FIRST and deliberately swallows photos: Telegram delivers an album as N separate
    messages, so each caption-less item would otherwise earn its own comment — all of
    them landing in the same discussion thread as the one the album's captioned head
    already got. Everything else (video, document, poll, sticker, link preview) carries
    nothing readable, and is grouped as ``other`` so the skip log can price it.
    """
    media = getattr(message, "media", None)
    if media is None:
        return "none"
    if getattr(message, "grouped_id", None) is not None:
        return "album"
    return "photo" if isinstance(media, MessageMediaPhoto) else "other"


def _make_handler(
    account_id: str,
    channel_by_peer_id: dict[int, str],
    on_post: Callable[[NewPostEvent], Awaitable[None]],
    *,
    generation: int | None = None,
) -> _EventHandler:
    async def handler(event: events.NewMessage.Event) -> None:
        # ``generation=None`` keeps this small factory useful in isolated tests and for
        # callers that only need event translation. Production subscriptions always pass
        # an ownership token, so a detached handler already queued by Telethon cannot
        # deliver into a replacement runtime.
        if generation is not None and _GENERATIONS.get(account_id) != generation:
            return
        message = event.message
        if message.post is not True:
            # Only channel broadcast posts; ``post`` is also falsy for megagroups.
            return
        channel = channel_by_peer_id.get(event.chat_id, str(event.chat_id))
        post = NewPostEvent(
            channel=channel,
            post_id=message.id,
            text=message.message or "",
            media_kind=_media_kind(message),
            is_forward=message.fwd_from is not None,
            date_unix=int(getattr(message, "date", datetime.now(UTC)).timestamp()),
        )
        try:
            await on_post(post)
        except Exception as exc:  # a callback fault must not kill the listener.
            logger.exception("listener callback failed for %s", channel)
            await log_event(
                "ERROR",
                "neurocomment_listener_callback_failed",
                account_id=account_id,
                extra={"channel": channel, "error_type": type(exc).__name__},
            )

    return handler


def _reset_for_tests() -> None:
    """Test-only reset; production code never calls this."""
    _HANDLERS.clear()
    _FILTERS.clear()
    _PEER_IDS.clear()
    _LOST_ACCESS.clear()
    _GENERATIONS.clear()
    _SUBSCRIPTION_LOCKS.clear()
