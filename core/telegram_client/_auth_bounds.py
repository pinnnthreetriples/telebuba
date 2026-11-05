"""Deadlines and failure classes shared by the sign-in flows (phone code, second device).

Telethon's request path awaits its response future with no timeout of its own, so a
dead or half-open proxy would hold an auth lock (and the pool tombstone) forever and
refuse every later login attempt for that account. Each flow therefore runs its
network steps under :func:`auth_deadline` and closes its client with
:func:`safe_disconnect`, which is bounded too and never raises.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from python_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError
from telethon import errors

from core.config import settings

if TYPE_CHECKING:
    from telethon import TelegramClient

# Stdlib sink: a transport failure stringifies with the proxy URL (see ``_pool``).
logger = logging.getLogger(__name__)

# Timeouts, dropped connections and proxy faults: nothing was learned about the
# account, so the operator is told to retry (or fix the proxy), not blamed.
UNREACHABLE_ERRORS = (
    ConnectionError,
    OSError,
    TimeoutError,
    ProxyConnectionError,
    ProxyError,
    ProxyTimeoutError,
)


def auth_deadline() -> float:
    """Overall seconds one sign-in network phase may take (connect + RPCs)."""
    return settings.telegram.session_check_timeout_seconds


async def safe_disconnect(client: TelegramClient) -> None:
    """Disconnect without ever hanging or raising: a stuck close must not hold the lock."""
    try:
        async with asyncio.timeout(settings.telegram.timeout_seconds):
            await client.disconnect()
    except Exception as exc:  # noqa: BLE001 - best effort; the verdict is already decided.
        logger.warning("auth client disconnect failed (%s)", type(exc).__name__)


def request_failure_code(exc: Exception) -> str:
    """Stable, locale-neutral code for a failed SendCode (the SPA translates it)."""
    if isinstance(exc, errors.FloodWaitError):
        return "flood_wait"
    if isinstance(exc, errors.PhoneNumberInvalidError):
        return "phone_invalid"
    if isinstance(exc, errors.PhoneNumberBannedError):
        return "phone_banned"
    if isinstance(exc, UNREACHABLE_ERRORS):
        return "unavailable"
    return "failed"
