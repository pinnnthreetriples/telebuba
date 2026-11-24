"""The strict "is this key signed in" probe shared by every caller that acts on a ``False``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from telethon import errors
from telethon.tl.functions.updates import GetStateRequest

if TYPE_CHECKING:
    from telethon import TelegramClient


async def probe_authorized(client: TelegramClient) -> bool:
    """Whether the session is signed in; ``False`` only when Telegram says it is not.

    Not Telethon's ``is_user_authorized()``: it answers ``False`` for ANY refusal of its
    probe (a flood wait, an internal error), and a "not authorized" starts a sign-in,
    marks the account dead or drops a subscription. A live key must never be judged on a
    hiccup, so anything but an authorization refusal (HTTP 401: ``AuthKeyUnregistered``,
    ``SessionRevoked`` and friends all subclass ``UnauthorizedError``, so a never-signed-in
    key still reads ``False``) is raised for the caller's own error handling.
    """
    try:
        await client(GetStateRequest())
    except errors.UnauthorizedError:
        return False
    return True
