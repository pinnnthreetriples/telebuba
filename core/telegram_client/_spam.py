"""@SpamBot probe + self-restriction read for one account."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from telethon import events

from core.config import settings
from core.logging import log_event
from core.telegram_client._client import telegram_client
from core.telegram_client._util import optional_str
from schemas.device_fingerprint import TelegramClientRequest
from schemas.spam_status import SpamStatusProbe

if TYPE_CHECKING:
    from telethon import TelegramClient

_SPAMBOT_USERNAME = "SpamBot"


async def check_spam_status(account_id: str) -> SpamStatusProbe:
    """Probe @SpamBot and read self-restriction flags for an account.

    Sends ``/start`` to @SpamBot and captures its reply, and reads the account's
    own ``restricted`` / ``restriction_reason`` flags via ``get_me``. The raw
    result is parsed and cached by ``services.spam_status`` — never raises.
    """
    request = TelegramClientRequest(account_id=account_id)
    async with telegram_client(request) as client:
        try:
            await client.connect()
            reply_text = await _probe_spambot(client)
            restricted, reason = await _probe_self_restriction(client)
        except Exception as exc:  # noqa: BLE001 - any probe failure classifies as unknown.
            await log_event(
                "WARNING",
                "telegram_spam_status_probe_failed",
                account_id=account_id,
                extra={"error_type": type(exc).__name__, "message": str(exc)},
            )
            return SpamStatusProbe(account_id=account_id, error=f"{type(exc).__name__}: {exc}")
    return SpamStatusProbe(
        account_id=account_id,
        reply_text=reply_text,
        restricted=restricted,
        restriction_reason=reason,
    )


async def _probe_spambot(client: TelegramClient) -> str | None:
    """Send ``/start`` to @SpamBot and return the next message it sends back.

    Uses ``events.NewMessage`` rather than ``client.conversation`` to close a
    documented race in Telethon: the conversation context registers its update
    handler inside ``__aenter__``, so a fast-replying bot can deliver its reply
    in the window between ``connect()`` and the handler being live. That reply
    then lands in the general update pool with no one waiting for it, and the
    conversation times out even though the bot did answer. The Telethon docs
    state the rule explicitly: "you should get a 'handle' of this special
    coroutine before acting."

    Here we register the handler BEFORE ``send_message``, filter by sender so
    we don't grab unrelated traffic, and coordinate via ``asyncio.Future``
    with our own ``wait_for`` timeout — the exact pattern the docs recommend
    for waiting on a single message.
    """
    bot = await client.get_input_entity(_SPAMBOT_USERNAME)
    loop = asyncio.get_running_loop()
    reply_future: asyncio.Future[str | None] = loop.create_future()

    async def on_reply(event: events.NewMessage.Event) -> None:
        if not reply_future.done():
            reply_future.set_result(optional_str(getattr(event, "raw_text", None)))

    client.add_event_handler(on_reply, events.NewMessage(from_users=bot, incoming=True))
    try:
        await client.send_message(bot, "/start")
        return await asyncio.wait_for(
            reply_future,
            timeout=settings.telegram.timeout_seconds,
        )
    finally:
        client.remove_event_handler(on_reply)


async def _probe_self_restriction(client: TelegramClient) -> tuple[bool, str | None]:
    """Read the account's own ``restricted`` flag + reason (terms/country)."""
    me = await client.get_me()
    restricted = bool(getattr(me, "restricted", False))
    reasons = getattr(me, "restriction_reason", None) or []
    reason = "; ".join(
        str(getattr(item, "text", "") or getattr(item, "reason", "")) for item in reasons
    ).strip("; ")
    return restricted, (reason or None)
