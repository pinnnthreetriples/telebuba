"""Standing inbound-message subscriptions on pooled Telegram clients."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from telethon import events

from core.telegram_client._pool import (
    get_client,
    register_rebuild_hook,
)
from schemas.inbox_events import InboxMessageReceived

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from telethon import TelegramClient

    _Handler = Callable[[events.NewMessage.Event], Awaitable[None]]

logger = logging.getLogger(__name__)

_HANDLERS: dict[str, _Handler] = {}
_FILTERS: dict[str, object] = {}
_HANDLER_CLIENTS: dict[str, TelegramClient] = {}
_LOCKS: dict[str, asyncio.Lock] = {}
_GENERATIONS: dict[str, int] = {}
_GENERATION_SEQUENCE = 0


def _lock(account_id: str) -> asyncio.Lock:
    lock = _LOCKS.get(account_id)
    if lock is None:
        lock = _LOCKS[account_id] = asyncio.Lock()
    return lock


def _next_generation(account_id: str) -> int:
    global _GENERATION_SEQUENCE  # noqa: PLW0603 - one process owns these handlers.
    _GENERATION_SEQUENCE += 1
    _GENERATIONS[account_id] = _GENERATION_SEQUENCE
    return _GENERATION_SEQUENCE


async def _reattach_on_rebuild(account_id: str, client: TelegramClient) -> None:
    """Restore the subscription after the account pool replaces its client."""
    async with _lock(account_id):
        handler = _HANDLERS.get(account_id)
        event_filter = _FILTERS.get(account_id)
        generation = _GENERATIONS.get(account_id)
        if handler is not None and event_filter is not None:
            client.add_event_handler(handler, event_filter)  # ty: ignore[invalid-argument-type]
            _HANDLER_CLIENTS[account_id] = client
    if (
        handler is not None
        and event_filter is not None
        and _GENERATIONS.get(account_id) == generation
    ):
        await client.catch_up()


register_rebuild_hook(_reattach_on_rebuild)


async def subscribe_incoming_messages(  # noqa: C901 - handler fencing and peer normalization.
    account_id: str,
    on_message: Callable[[InboxMessageReceived], Awaitable[None]],
) -> bool:
    """Watch every peer on one authorized pooled account; return False if unauthorized."""
    async with _lock(account_id):
        generation = _next_generation(account_id)
        _detach_locked(account_id)

    client = await get_client(account_id)
    if not await client.is_user_authorized():
        return False
    event_filter = events.NewMessage(incoming=True)

    async def handler(event: events.NewMessage.Event) -> None:
        if _GENERATIONS.get(account_id) != generation:
            return
        message = event.message
        peer = getattr(message, "peer_id", None)
        if peer is None:
            return
        if hasattr(peer, "user_id"):
            peer_type, peer_id = "user", peer.user_id
        elif hasattr(peer, "chat_id"):
            peer_type, peer_id = "chat", peer.chat_id
        elif hasattr(peer, "channel_id"):
            peer_type, peer_id = "channel", peer.channel_id
        else:
            return
        message_id = int(getattr(message, "id", 0) or 0)
        if message_id <= 0 or not peer_id:
            return
        try:
            await on_message(
                InboxMessageReceived(
                    account_id=account_id,
                    peer_type=peer_type,
                    peer_id=str(peer_id),
                    message_id=message_id,
                ),
            )
        except Exception as exc:  # one update must not tear down the subscription.
            logger.exception("inbox update callback failed for %s", account_id)
            from core.logging import log_event  # noqa: PLC0415 - avoid import cycle.

            await log_event(
                "ERROR",
                "inbox_message_callback_failed",
                account_id=account_id,
                extra={"error_type": type(exc).__name__},
            )

    async with _lock(account_id):
        if _GENERATIONS.get(account_id) != generation:
            return False
        client.add_event_handler(handler, event_filter)
        _HANDLERS[account_id] = handler
        _FILTERS[account_id] = event_filter
        _HANDLER_CLIENTS[account_id] = client
    if _GENERATIONS.get(account_id) == generation:
        await client.catch_up()
    return True


async def ensure_incoming_message_connection(account_id: str) -> bool:
    """Borrow/rebuild the pooled client and ensure its current handler is attached."""
    client = await get_client(account_id)
    async with _lock(account_id):
        handler = _HANDLERS.get(account_id)
        event_filter = _FILTERS.get(account_id)
        if handler is None or event_filter is None:
            return False
        if _HANDLER_CLIENTS.get(account_id) is not client:
            client.add_event_handler(handler, event_filter)  # ty: ignore[invalid-argument-type]
            _HANDLER_CLIENTS[account_id] = client
    return True


async def stop_incoming_messages(account_id: str, *, forget: bool = False) -> None:
    """Detach without connecting a client; optionally release per-account registry state."""
    async with _lock(account_id):
        _next_generation(account_id)
        _detach_locked(account_id)
        if forget:
            _GENERATIONS.pop(account_id, None)
    if forget:
        _LOCKS.pop(account_id, None)


async def forget_incoming_message_listener(account_id: str) -> None:
    """Stop and release registry state after the account row has been removed."""
    await stop_incoming_messages(account_id, forget=True)


def _detach_locked(account_id: str) -> None:
    handler = _HANDLERS.pop(account_id, None)
    event_filter = _FILTERS.pop(account_id, None)
    client = _HANDLER_CLIENTS.pop(account_id, None)
    if handler is not None and event_filter is not None and client is not None:
        client.remove_event_handler(handler, event_filter)  # ty: ignore[invalid-argument-type]


def _reset_for_tests() -> None:
    """Clear module registries between tests."""
    global _GENERATION_SEQUENCE  # noqa: PLW0603
    _HANDLERS.clear()
    _FILTERS.clear()
    _HANDLER_CLIENTS.clear()
    _LOCKS.clear()
    _GENERATIONS.clear()
    _GENERATION_SEQUENCE = 0
