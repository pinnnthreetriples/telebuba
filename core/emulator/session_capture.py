"""Intercept the 5-digit Telegram login code from service chat ``777000``."""

# ruff: noqa: ASYNC109

from __future__ import annotations

import asyncio
import re
import time
from typing import TYPE_CHECKING

from core.emulator.ldconsole import EmulatorError
from core.telegram_client._pool import get_client

if TYPE_CHECKING:
    from datetime import datetime

__all__ = [
    "TELEGRAM_SERVICE_USER_ID",
    "LoginCodeTimeoutError",
    "extract_login_code",
    "wait_for_telegram_login_code",
]

TELEGRAM_SERVICE_USER_ID = 777000
_CODE_RE = re.compile(r"\b(\d{5})\b")


class LoginCodeTimeoutError(EmulatorError):
    """Telethon did not receive a 5-digit login code from ``777000`` within the timeout."""


def extract_login_code(text: str | None) -> str | None:
    """Extract a 5-digit Telegram login code from ``777000`` service message text."""
    if not text:
        return None
    match = _CODE_RE.search(text)
    return match.group(1) if match is not None else None


async def wait_for_telegram_login_code(
    account_id: str,
    *,
    timeout: float = 45.0,
    poll_interval: float = 1.0,
    min_timestamp: datetime | None = None,
) -> str:
    """Poll ``777000`` on the account's pooled Desktop session until a 5-digit code arrives."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        client = await get_client(account_id, device_type="desktop")
        messages = await client.get_messages(TELEGRAM_SERVICE_USER_ID, limit=5)
        items = (
            list(messages)
            if isinstance(messages, (list, tuple))
            else ([messages] if messages is not None else [])
        )
        for msg in items:
            msg_date = getattr(msg, "date", None)
            if min_timestamp is not None and msg_date is not None and msg_date < min_timestamp:
                continue
            raw_text = getattr(msg, "message", None) or getattr(msg, "raw_text", None)
            code = extract_login_code(raw_text if isinstance(raw_text, str) else None)
            if code is not None:
                return code
        await asyncio.sleep(poll_interval)
    msg = f"Timed out after {timeout}s waiting for 777000 login code on {account_id}"
    raise LoginCodeTimeoutError(msg)
