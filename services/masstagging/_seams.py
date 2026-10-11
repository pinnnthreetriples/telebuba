"""Injectable seams, centralised so tests patch them in one place.

The masstagging domain reaches Telegram (``execute`` / ``execute_read``), randomness,
sleeping and the clock only through this module — the shape ``services.chat_broadcast
._seams`` has, with the same fence: Pause bumps the run GENERATION, :func:`run_scope`
binds a run's tasks to theirs, and it is checked before and after each external call,
the "after" for a call already in flight when Pause was pressed. Calls run under the
account lifecycle lock (``account_lock``), never across a pause.
"""

from __future__ import annotations

import random
import time
from asyncio import sleep
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from core.telegram_client import execute as _gateway_execute
from core.telegram_client import execute_read as _gateway_execute_read

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from pydantic import BaseModel

    from schemas.telegram_actions import ActionResult, TelegramAction, TelegramReadAction

_DOMAIN = "masstagging"
_RUN_CURRENT: ContextVar[Callable[[], bool] | None] = ContextVar(
    "masstagging_run_current", default=None
)


class MasstaggingRunRevokedError(RuntimeError):
    """A paused run generation attempted external I/O."""


@contextmanager
def run_scope(is_current: Callable[[], bool]) -> Iterator[None]:
    """Bind a run's tasks to its generation for the duration of the block."""
    token = _RUN_CURRENT.set(is_current)
    try:
        yield
    finally:
        _RUN_CURRENT.reset(token)


def assert_live_run() -> None:
    is_current = _RUN_CURRENT.get()
    if is_current is not None and not is_current():
        raise MasstaggingRunRevokedError


async def execute(account_id: str, action: TelegramAction) -> ActionResult:
    """Dispatch one Telegram write under the account lifecycle lock."""
    from services.warming import account_lock  # noqa: PLC0415 - avoids an import cycle

    assert_live_run()
    async with account_lock(account_id):
        assert_live_run()
        result = await _gateway_execute(account_id, action, domain=_DOMAIN)
        assert_live_run()
        return result


async def execute_read(account_id: str, action: TelegramReadAction) -> BaseModel:
    """One Telegram read under the same lock."""
    from services.warming import account_lock  # noqa: PLC0415 - avoids an import cycle

    assert_live_run()
    async with account_lock(account_id):
        assert_live_run()
        result = await _gateway_execute_read(account_id, action)
        assert_live_run()
        return result


def now() -> int:
    """Unix seconds — the engine's one clock, so tests can move it."""
    return int(time.time())


# SystemRandom: non-cryptographic jitter/selection without ruff S311.
rng = random.SystemRandom()

__all__ = [
    "MasstaggingRunRevokedError",
    "assert_live_run",
    "execute",
    "execute_read",
    "now",
    "rng",
    "run_scope",
    "sleep",
]
