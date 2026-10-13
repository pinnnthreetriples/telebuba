"""Read a Telegram login code off the account's EXISTING session (service chat 777000).

When an account is already signed in somewhere, Telegram delivers a new device's
login code as a service message from 777000 to that session. The code is taken
only from a message NEWER than the id snapshotted before the code was requested:
message ids are assigned by Telegram, so unlike a date comparison against the host
clock the cut-off cannot drop a fresh code or accept an old one.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, cast

from telethon import errors

from core.telegram_client._pool import TelegramClientPoolError, get_client

if TYPE_CHECKING:
    from collections.abc import Iterable

    from telethon import TelegramClient

# The existing session failing to read 777000 at all: transport or an RPC refusal.
_SESSION_FAILURES = (ConnectionError, TimeoutError, errors.RPCError)

SERVICE_USER_ID = 777000
DEFAULT_CODE_LENGTH = 5
# Labeled forms first ("Login code: 12345", "Код для входа: 12345"), then a bare
# number of exactly the expected length — never a longer one cut short.
_LABELS = r"(?:login\s*code|код(?:\s*(?:для\s*входа|подтверждения))?|code)"


def extract_login_code(text: str | None, length: int = DEFAULT_CODE_LENGTH) -> str | None:
    """The ``length``-digit login code in a 777000 message, or ``None``."""
    if not text:
        return None
    digits = rf"(\d{{{length}}})(?![0-9])"
    for pattern in (rf"(?i){_LABELS}[:\s]+{digits}", rf"(?<![0-9]){digits}"):
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


async def _service_messages(client: TelegramClient, *, after_id: int, limit: int) -> list[object]:
    try:
        messages = await client.get_messages(SERVICE_USER_ID, limit=limit, min_id=after_id)
    except ValueError:
        # 777000 not in this session's entity cache yet (a freshly imported session):
        # listing recent dialogs caches it, then the read is retried once.
        await client.get_dialogs(limit=20)
        try:
            messages = await client.get_messages(SERVICE_USER_ID, limit=limit, min_id=after_id)
        except ValueError:
            # Still unknown: no 777000 chat among the recent ones, so nothing to see yet.
            # A code that arrives puts that chat on top, where the next poll finds it.
            return []
    return list(cast("Iterable[object]", messages or []))


async def last_service_message_id(client: TelegramClient) -> int:
    """The newest 777000 message id now (0 when there is none) — the code cut-off."""
    messages = await _service_messages(client, after_id=0, limit=1)
    return max((int(getattr(message, "id", 0) or 0) for message in messages), default=0)


async def wait_for_login_code(
    client: TelegramClient,
    *,
    after_id: int,
    wait_seconds: float,
    poll_seconds: float,
    length: int = DEFAULT_CODE_LENGTH,
) -> str | None:
    """Poll for a code in a 777000 message newer than ``after_id``; ``None`` on timeout."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_seconds
    while True:
        for message in await _service_messages(client, after_id=after_id, limit=5):
            if int(getattr(message, "id", 0) or 0) <= after_id:
                continue
            code = extract_login_code(getattr(message, "message", None), length)
            if code is not None:
                return code
        if loop.time() + poll_seconds > deadline:
            return None
        await asyncio.sleep(poll_seconds)


async def login_code_cutoff(account_id: str) -> int:
    """The newest 777000 id the account's primary session sees, taken before a code is asked."""
    try:
        return await last_service_message_id(await get_client(account_id))
    except _SESSION_FAILURES as exc:
        raise TelegramClientPoolError(account_id, exc) from exc


async def wait_account_login_code(
    account_id: str,
    *,
    after_id: int,
    wait_seconds: float,
    poll_seconds: float,
) -> str | None:
    """A login code that reached the account's primary session after ``after_id``."""
    try:
        return await wait_for_login_code(
            await get_client(account_id),
            after_id=after_id,
            wait_seconds=wait_seconds,
            poll_seconds=poll_seconds,
        )
    except _SESSION_FAILURES as exc:
        raise TelegramClientPoolError(account_id, exc) from exc
