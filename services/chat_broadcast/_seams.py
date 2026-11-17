"""Injectable seams, centralised so tests patch them in one place.

The broadcast domain reaches Telegram (``execute`` / ``execute_read``), the LLMs, the
captcha solver, randomness, sleeping and the clock only through this module — the shape
``services.neuroshilling._seams`` has, with the same two fences:

* **The run generation.** Stop bumps it; :func:`run_scope` binds a run's tasks to their
  generation and the fence is checked BEFORE and AFTER each external call, the "after"
  for a call already in flight when Stop was pressed.
* **The pacer.** ``services.pacing.await_send_slot`` is awaited OUTSIDE ``account_lock``,
  which is the account lifecycle mutex and must not be held across a pause.
"""

from __future__ import annotations

import logging
import random
import time
from asyncio import sleep
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Literal

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
    from schemas.spam_status import SpamStatusKind
    from schemas.telegram_actions import ActionResult, TelegramAction, TelegramReadAction
    from services.neurocomment.challenge import ChallengeOutcome

logger = logging.getLogger(__name__)

_DOMAIN = "chat_broadcast"
_RUN_CURRENT: ContextVar[Callable[[], bool] | None] = ContextVar(
    "chat_broadcast_run_current", default=None
)


class ChatBroadcastRunRevokedError(RuntimeError):
    """A stopped run generation attempted external I/O."""


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
        raise ChatBroadcastRunRevokedError


async def execute(account_id: str, action: TelegramAction) -> ActionResult:
    """Pace, then dispatch one Telegram write under the account lifecycle lock."""
    from services.warming import account_lock  # noqa: PLC0415 - avoids an import cycle

    assert_live_run()
    await await_send_slot(account_id, settings.chat_broadcast.send_min_gap_seconds)
    async with account_lock(account_id):
        assert_live_run()
        result = await _gateway_execute(account_id, action, domain=_DOMAIN)
        assert_live_run()
        return result


async def execute_read(account_id: str, action: TelegramReadAction) -> BaseModel:
    """One Telegram read under the same lock; reads are not paced."""
    from services.warming import account_lock  # noqa: PLC0415 - avoids an import cycle

    async with account_lock(account_id):
        assert_live_run()
        result = await _gateway_execute_read(account_id, action)
        assert_live_run()
        return result


async def generate_text(request: GeminiRequest) -> GeminiResult:
    assert_live_run()
    result = await _generate_text(request)
    assert_live_run()
    return result


async def generate_text_deepseek(request: GeminiRequest) -> GeminiResult:
    assert_live_run()
    result = await _generate_text_deepseek(request)
    assert_live_run()
    return result


async def solve_challenge(account_id: str, chat: str, group_id: int) -> ChallengeOutcome:
    """Neurocomment's guardian-bot solver, reused — never copied.

    Its audit row and feed lines stay neurocomment's; a crash inside it is a failed
    challenge for this chat, not a failed run.
    """
    from services.neurocomment import solve_join_challenge  # noqa: PLC0415 - import cycle

    assert_live_run()
    try:
        outcome = await solve_join_challenge(account_id, chat, group_id)
    except ChatBroadcastRunRevokedError:
        raise
    except Exception:
        logger.exception("captcha solver failed for %s", account_id)
        outcome = "failed"
    assert_live_run()
    return outcome


@contextmanager
def spam_caller(label: str) -> Iterator[None]:
    """Name this domain's reason for a @SpamBot question (events, ledger rows)."""
    from services.spam_intel import caller_scope  # noqa: PLC0415 - import cycle

    with caller_scope(label):
        yield


async def spam_verdict(account_id: str) -> SpamStatusKind | Literal["deferred"]:
    """Ask @SpamBot afresh; a probe that failed reads ``unknown``, a postponed one ``deferred``.

    Never the cache: a ``clean`` from yesterday says nothing about the refusal the account
    just got. A reading younger than the gateway's coalescing window is still handed back,
    and the budget may say "later" (``deferred``): that is NOT a failed probe, so the run
    keeps the account resting instead of treating it as a block.
    """
    from schemas.spam_status import is_unasked  # noqa: PLC0415 - import cycle
    from services.spam_intel import ask  # noqa: PLC0415 - import cycle

    assert_live_run()
    verdict = await ask(account_id, force=True, holder=_DOMAIN)
    assert_live_run()
    if verdict.served_from == "deferred":
        return "deferred"
    if is_unasked(verdict):
        return "unknown"
    return verdict.status


async def known_spam_status(account_id: str) -> SpamStatusKind:
    """What is ALREADY known about the account's spam status: no Telegram, no probe.

    The stored reading as every decision reader sees it (``spam_intel.decision_status``): a dated
    limit that has ended reads ``unknown``, a fold that says ``limited`` reads ``limited`` when
    passive decisions are on. An account nobody has read reads ``unknown``, which never rests it.
    """
    from core.db import get_spam_status  # noqa: PLC0415 - import cycle
    from services.spam_intel import decision_status  # noqa: PLC0415 - import cycle

    view = await decision_status(account_id, await get_spam_status(account_id))
    return "unknown" if view is None else view.status


def now() -> int:
    """Unix seconds — the engine's one clock, so tests can move it."""
    return int(time.time())


# SystemRandom: non-cryptographic jitter/selection without ruff S311.
rng = random.SystemRandom()

__all__ = [
    "ChatBroadcastRunRevokedError",
    "assert_live_run",
    "await_send_slot",
    "execute",
    "execute_read",
    "generate_text",
    "generate_text_deepseek",
    "known_spam_status",
    "now",
    "rng",
    "run_scope",
    "sleep",
    "solve_challenge",
    "spam_caller",
    "spam_verdict",
]
