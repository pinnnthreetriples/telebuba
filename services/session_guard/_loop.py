"""The session guard's one background task: what must be done about sessions, in the background.

Started in the lifespan next to the survival probes and stopped before the client pool, since
every step borrows a pooled client. One pass this often (``SESSION_AUDIT__GUARD_INTERVAL_SECONDS``),
each step guarded on its own so one failing never ends the loop or starves the other:

* old authorizations of re-keyed accounts whose time has come
  (``services.accounts.close_due_old_keys``);
* the watch: one account's sessions re-read, if one is due (``_watch``).

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


async def _guarded(step: str, run: Callable[[], Awaitable[int | str | None]]) -> int:
    """One step; how many things it did. A failure is logged and never raised."""
    try:
        done = await run()
    except Exception as exc:  # noqa: BLE001 - one failed step must not end the loop.
        logger.warning("session guard step failed", exc_info=exc)
        await log_event(
            "WARNING",
            "session_guard_failed",
            extra={"step": step, "error_type": type(exc).__name__},
        )
        return 0
    return done if isinstance(done, int) else int(done is not None)


async def run_guard_pass() -> int:
    """One pass over every step; how many old keys it ended plus accounts it watched."""
    ended = await _guarded("old_keys", close_due_old_keys)
    watched = await _guarded("watch", run_watch_pass)
    return ended + watched


async def _loop() -> None:
    while True:
        await run_guard_pass()
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
