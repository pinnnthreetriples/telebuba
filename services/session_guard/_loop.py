"""The session guard's one background task: what must be done about sessions, in the background.

Started in the lifespan next to the survival probes and stopped before the client pool, since
every step borrows a pooled client. One pass this often (``SESSION_AUDIT__GUARD_INTERVAL_SECONDS``),
each step guarded on its own so one failing never ends the loop or starves the other:

* old authorizations of re-keyed accounts whose time has come
  (``services.accounts.close_due_old_keys``);
* the watch: one account's sessions re-read, if one is due (``_watch``).

Guarantees: a step that fails in ANY way is logged as ``session_guard_failed`` and the loop
goes on, including a ``CancelledError`` that did not come from cancelling this task and a step
that outlives ``SESSION_AUDIT__GUARD_STEP_TIMEOUT_SECONDS`` (its cancellation unwinds the
per-account audit lock). In production the guard once went silent for 41 hours right after
ending an old key, with no ``session_guard_failed`` logged; whether a stray cancellation or a
hang did it is unknown, so both are covered. Only cancelling the task itself
(``shutdown_session_guard``) ends the loop; KeyboardInterrupt and SystemExit are never caught.

Everything here is in memory and one uvicorn worker runs, so there is no coordination beyond
the per-account audit lock the steps take.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from services.accounts import close_due_old_keys
from services.session_guard._watch import run_watch_pass

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# Stdlib sink for the traceback; the persisted event carries only the error type.
logger = logging.getLogger(__name__)


@dataclass
class _State:
    task: asyncio.Task[None] | None = None


_state = _State()


async def _log_failure(step: str, exc: BaseException) -> None:
    logger.warning("session guard step failed", exc_info=exc)
    await log_event(
        "WARNING",
        "session_guard_failed",
        extra={"step": step, "error_type": type(exc).__name__},
    )


def _own_cancellation() -> bool:
    """Whether this task itself is being cancelled (shutdown), not a stray ``CancelledError``.

    ``Task.cancelling()`` counts the ``cancel()`` calls made on this task; a ``CancelledError``
    raised from inside a step by a library's own cancelled future leaves it at zero.
    """
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


async def _guarded(step: str, run: Callable[[], Awaitable[int | str | None]]) -> int:
    """One step, under a deadline; how many things it did. A failure is logged, never raised.

    Only the task's own cancellation propagates: a stray ``CancelledError`` and the deadline's
    ``TimeoutError`` are step failures, since either one escaping used to end the whole guard.
    """
    try:
        async with asyncio.timeout(settings.session_audit.guard_step_timeout_seconds):
            done = await run()
    except (Exception, asyncio.CancelledError) as exc:  # one failed step must not end the loop.
        if isinstance(exc, asyncio.CancelledError) and _own_cancellation():
            raise
        await _log_failure(step, exc)
        return 0
    return done if isinstance(done, int) else int(done is not None)


async def run_guard_pass() -> int:
    """One pass over every step; how many old keys it ended plus accounts it watched."""
    ended = await _guarded("old_keys", close_due_old_keys)
    watched = await _guarded("watch", run_watch_pass)
    return ended + watched


async def _loop() -> None:
    while True:
        try:
            await run_guard_pass()
        except (Exception, asyncio.CancelledError) as exc:  # the loop must outlive anything.
            if isinstance(exc, asyncio.CancelledError) and _own_cancellation():
                raise
            await _log_failure("loop", exc)
        await asyncio.sleep(settings.session_audit.guard_interval_seconds)


async def start_session_guard() -> None:
    """Start the background task (lifespan startup); a no-op when it is already running."""
    if _state.task is not None:
        return
    _state.task = asyncio.create_task(_loop(), name="session-guard")


async def shutdown_session_guard() -> None:
    """Cancel the task and wait for it to unwind."""
    task, _state.task = _state.task, None
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def reset_for_tests() -> None:
    """Forget the task between tests."""
    global _state  # noqa: PLW0603 - one module-level runtime, reset between tests
    _state = _State()
