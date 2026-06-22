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

from typing import TYPE_CHECKING

from telethon import events

from core.logging import log_event
from core.telegram_client._pool import get_client
from schemas.telegram_actions import NewPostEvent

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    _EventHandler = Callable[[events.NewMessage.Event], Awaitable[None]]

__all__ = [
    "stop_post_listener",
    "subscribe_posts",
    "update_post_subscription",
]


# ponytail: single-process registry. One app instance owns the listener account;
# a multi-process deployment would need this state in shared storage instead.
_HANDLERS: dict[str, _EventHandler] = {}


async def subscribe_posts(
    account_id: str,
    channels: list[str],
    on_post: Callable[[NewPostEvent], Awaitable[None]],
) -> None:
    """Register a single ``NewMessage`` handler watching ``channels`` for new posts.

    Idempotent: re-subscribing for the same account first removes the prior
    handler, so it is safe to call on every app start. ``channels`` is the
    whitelist — only those channels fire. Each fresh broadcast post is mapped
    back to its original subscription string and pushed to ``on_post``; a
    callback error is logged and swallowed so it can't kill the listener.
    """
    await stop_post_listener(account_id)

    client = await get_client(account_id)
    channel_by_peer_id: dict[int, str] = {}
    for channel in channels:
        peer_id = await client.get_peer_id(channel)
        channel_by_peer_id[peer_id] = channel

    handler = _make_handler(channel_by_peer_id, on_post)
    client.add_event_handler(handler, events.NewMessage(chats=channels))
    _HANDLERS[account_id] = handler


async def update_post_subscription(
    account_id: str,
    channels: list[str],
    on_post: Callable[[NewPostEvent], Awaitable[None]],
) -> None:
    """Swap the watched set: drop the old handler, register the new one."""
    await subscribe_posts(account_id, channels, on_post)


async def stop_post_listener(account_id: str) -> None:
    """Remove the account's handler and clear its state; no-op if none."""
    handler = _HANDLERS.pop(account_id, None)
    if handler is None:
        return
    client = await get_client(account_id)
    # Telethon accepts the EventBuilder *class* here to drop all NewMessage
    # handlers; its stub only types the instance form.
    client.remove_event_handler(handler, events.NewMessage)  # ty: ignore[invalid-argument-type]


def _make_handler(
    channel_by_peer_id: dict[int, str],
    on_post: Callable[[NewPostEvent], Awaitable[None]],
) -> _EventHandler:
    async def handler(event: events.NewMessage.Event) -> None:
        message = event.message
        if message.post is not True:
            # Only channel broadcast posts; ``post`` is also falsy for megagroups.
            return
        channel = channel_by_peer_id.get(event.chat_id, str(event.chat_id))
        post = NewPostEvent(
            channel=channel,
            post_id=message.id,
            text=message.message or "",
            has_media=message.media is not None,
        )
        try:
            await on_post(post)
        except Exception as exc:  # noqa: BLE001 — a callback fault must not kill the listener.
            await log_event(
                "ERROR",
                "post_listener_callback_failed",
                extra={"channel": channel, "error_type": type(exc).__name__, "message": str(exc)},
            )

    return handler


def _reset_for_tests() -> None:
    """Test-only reset; production code never calls this."""
    _HANDLERS.clear()
