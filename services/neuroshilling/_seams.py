"""Injectable seams, centralised so tests patch them in one place.

The neuroshilling domain reaches Telegram (``execute`` / ``execute_read``), the
LLMs (``generate_text_deepseek``, ``generate_text``), randomness (``rng``) and
sleeping (``sleep``) only through this module, so a test patches
``services.neuroshilling._seams.<name>`` once and every submodule observes it.
Mirrors ``services.neurocomment._seams``.

Two fences sit on every external call:

* **The run generation.** ``Stop`` is not a status flip — a status flip returns
  "stopped" while N coroutines are still asleep inside a step delay and will
  wake up and post. :func:`run_scope` binds a background task to its generation
  and the fence is checked BEFORE and AFTER each call; the "after" half is for
  the call that was already in flight when Stop was pressed, whose outcome is
  unknown and must not be built on.
* **The pacer.** ``services.pacing.await_send_slot`` is awaited OUTSIDE
  ``account_lock`` — deliberately. That lock is the account lifecycle mutex, and
  sleeping inside it would freeze Start/Stop/remove for the account for as long
  as the pause lasts.
"""

from __future__ import annotations

import random
from asyncio import sleep
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic
from typing import TYPE_CHECKING

from core.config import settings
from core.gemini import generate_text as _generate_text
from core.openai import generate_text_deepseek as _generate_text_deepseek
from core.telegram_client import execute as _gateway_execute
from core.telegram_client import execute_read as _gateway_execute_read
from services.pacing import await_send_slot

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from pydantic import BaseModel

    from schemas.gemini import GeminiRequest, GeminiResult
    from schemas.telegram_actions import ActionResult, TelegramAction, TelegramReadAction

_RUN_CURRENT: ContextVar[Callable[[], bool] | None] = ContextVar(
    "neuroshilling_run_current",
    default=None,
)


class NeuroshillingRunRevokedError(RuntimeError):
    """A stopped run generation attempted external I/O."""


@contextmanager
def run_scope(is_current: Callable[[], bool]) -> Iterator[None]:
    """Bind a background run task to its generation for the duration of the block."""
    token = _RUN_CURRENT.set(is_current)
    try:
        yield
    finally:
        _RUN_CURRENT.reset(token)


def _assert_live_run() -> None:
    is_current = _RUN_CURRENT.get()
    if is_current is not None and not is_current():
        raise NeuroshillingRunRevokedError


async def execute(account_id: str, action: TelegramAction) -> ActionResult:
    """Pace, then dispatch one Telegram write under the account lifecycle lock."""
    from services.warming import account_lock  # noqa: PLC0415 - avoids an import cycle

    _assert_live_run()
    await await_send_slot(account_id, settings.neuroshilling.send_min_gap_seconds)
    async with account_lock(account_id):
        _assert_live_run()
        result = await _gateway_execute(account_id, action, domain="neuroshilling")
        _assert_live_run()
        return result


async def execute_read(account_id: str, action: TelegramReadAction) -> BaseModel:
    """Dispatch one Telegram read under the same lifecycle lock.

    Not paced: the gate exists to space out what we PUBLISH, and slowing reads
    down would only make resolving a target take longer for no anti-ban gain.
    """
    from services.warming import account_lock  # noqa: PLC0415 - avoids an import cycle

    async with account_lock(account_id):
        _assert_live_run()
        result = await _gateway_execute_read(account_id, action)
        _assert_live_run()
        return result


async def generate_text_deepseek(request: GeminiRequest) -> GeminiResult:
    _assert_live_run()
    result = await _generate_text_deepseek(request)
    _assert_live_run()
    return result


async def generate_text(request: GeminiRequest) -> GeminiResult:
    _assert_live_run()
    result = await _generate_text(request)
    _assert_live_run()
    return result


# SystemRandom: non-cryptographic jitter/selection; avoids ruff S311 on the
# module-level ``random.*`` helpers. Behaviour is identical for our needs.
rng = random.SystemRandom()

__all__ = [
    "NeuroshillingRunRevokedError",
    "execute",
    "execute_read",
    "generate_text",
    "generate_text_deepseek",
    # Beside ``sleep`` because the two are one seam: the listening window is a
    # deadline measured against this clock and closed by those pauses, so a test
    # that stubs one without the other is measuring a window nothing advances.
    "monotonic",
    "rng",
    "run_scope",
    "sleep",
]
