"""Telegram service notification (777000) verification code extraction."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

from telethon.errors import RPCError

from core.emulator.config import LoginCodeTimeoutError
from core.telegram_client import get_client

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from telethon import TelegramClient

_TELEGRAM_SERVICE_PEER = 777000

# Regex patterns prioritized from most specific to generic 5-digit boundary
_CODE_PATTERNS = [
    # English specific
    re.compile(r"(?i)(?<![A-Za-z0-9])login\s*code[:\s]+(\d{5})(?![A-Za-z0-9])"),
    # Russian specific
    re.compile(
        r"(?i)(?<![A-Za-z0-9])код(?:\s*(?:для\s*входа|подтверждения))?[:\s]+(\d{5})(?![A-Za-z0-9])"
    ),
    # Generic "code:" or "code "
    re.compile(r"(?i)(?<![A-Za-z0-9])code[:\s]+(\d{5})(?![A-Za-z0-9])"),
    # Isolated 5-digit sequence bounded by non-alphanumeric chars
    re.compile(r"(?<![A-Za-z0-9])(\d{5})(?![A-Za-z0-9])"),
]


def extract_verification_code(text: str) -> str | None:
    """Extract a 5-digit login verification code from message text.

    Returns the first matching 5-digit code string, or None if no valid
    code is found. Rejects 4-digit or 6+-digit numbers.
    """
    if not text:
        return None

    # First check explicitly labeled code patterns
    for pattern in _CODE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)

    return None


async def poll_for_login_code(
    account_id: str,
    since_timestamp: float | None = None,
    timeout_seconds: float = 45.0,
    poll_interval: float = 1.5,
    client: TelegramClient | None = None,
) -> str:
    """Poll the account's active Telethon session for login code from 777000.

    Waits up to timeout_seconds for a message from Telegram's service user (777000)
    received on or after since_timestamp. Extracts and returns the 5-digit code.
    Raises LoginCodeTimeoutError if no code arrives before the deadline.
    """
    tg_client = client or await get_client(account_id)
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        try:
            raw_messages = await tg_client.get_messages(_TELEGRAM_SERVICE_PEER, limit=5)
            messages = raw_messages if isinstance(raw_messages, list) else []
            for msg in messages:
                if msg is None:
                    continue

                if since_timestamp is not None:
                    msg_date = getattr(msg, "date", None)
                    if msg_date is None or msg_date.timestamp() < since_timestamp:
                        continue

                text = getattr(msg, "raw_text", None) or getattr(msg, "message", "") or ""
                code = extract_verification_code(text)
                if code:
                    return code
        except (RPCError, ConnectionError, OSError, TimeoutError) as exc:
            # Continue polling through transient network hiccups
            logger.debug(
                "Transient failure while polling login code for %s: %s",
                account_id,
                exc,
            )

        await asyncio.sleep(poll_interval)

    msg = f"No login code received from 777000 for account {account_id} within {timeout_seconds}s"
    raise LoginCodeTimeoutError(msg)
