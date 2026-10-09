"""Injectable seams, centralised so tests patch them in one place.

The parser reaches Telegram (``execute`` / ``execute_read``), the fleet's cooldown map,
randomness, sleeping and the clock only through this module. Every Telegram call is made
under ``account_lock`` — warming's per-account lifecycle mutex — for the length of the
call only; the pauses between calls are slept outside it.
"""

from __future__ import annotations

import random
from asyncio import sleep
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.telegram_client import execute as _gateway_execute
from core.telegram_client import execute_read as _gateway_execute_read
from services.pacing import await_send_slot

if TYPE_CHECKING:
    from pydantic import BaseModel

    from schemas.telegram_actions import ActionResult, TelegramAction, TelegramReadAction

_DOMAIN = "user_parser"


async def execute_read(account_id: str, action: TelegramReadAction) -> BaseModel:
    """One Telegram read under the account's lifecycle lock; pacing is the caller's."""
    from services.warming import account_lock  # noqa: PLC0415 - avoids an import cycle

    async with account_lock(account_id):
        return await _gateway_execute_read(account_id, action)


async def execute(account_id: str, action: TelegramAction) -> ActionResult:
    """One Telegram write (a join) under the same lock, logged under this domain."""
    from services.warming import account_lock  # noqa: PLC0415 - avoids an import cycle

    async with account_lock(account_id):
        return await _gateway_execute(account_id, action, domain=_DOMAIN)


async def set_cooldown(account_id: str, until: datetime) -> None:
    """Park the account fleet-wide, so every other feature sees the parser's flood."""
    from services.neurocomment._state import set_cooldown as _set  # noqa: PLC0415 - cycle

    await _set(account_id, until)


def in_cooldown(account_id: str) -> bool:
    from services.neurocomment._state import in_cooldown as _in  # noqa: PLC0415 - cycle

    return _in(account_id, now())


def now() -> datetime:
    """The parser's one clock, so tests can move it."""
    return datetime.now(UTC)


# SystemRandom: non-cryptographic jitter without ruff S311.
rng = random.SystemRandom()

__all__ = [
    "await_send_slot",
    "execute",
    "execute_read",
    "in_cooldown",
    "now",
    "rng",
    "set_cooldown",
    "sleep",
]
