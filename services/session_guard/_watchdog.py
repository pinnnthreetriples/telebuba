"""Watch the watcher: the session guard must never go silent unnoticed again.

On 2026-11-14 the guard stopped for 41 hours and nothing said so. Its loop now outlives
any failed step (``_loop``), but a stall can still have causes nobody foresaw. So a second,
tiny task looks at the guard's heartbeat once per guard interval. A guard whose task ended
outside a shutdown, or that has done nothing for longer than a step's deadline plus two
passes, is logged once as ``session_guard_stalled`` (the operator bot forwards it); an
ended task is started again. The warning repeats only after the guard has recovered.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

from core.config import settings
from core.logging import log_event
from services.session_guard._loop import guard_status, restart_session_guard

__all__ = ["check_guard", "reset_watchdog_for_tests", "start_watchdog", "stop_watchdog"]


@dataclass
class _Watchdog:
    task: asyncio.Task[None] | None = None
    # Set once a stall is reported, cleared when the guard is healthy again.
    warned: bool = False


_dog = _Watchdog()


async def check_guard() -> None:
    """One look at the heartbeat: report a stall once, restart a guard that ended."""
    status = guard_status()
    if not status.stalled:
        _dog.warned = False
        return
    if not _dog.warned:
        _dog.warned = True
        await log_event(
            "WARNING",
            "session_guard_stalled",
            extra={"reason": status.reason, "last_activity_at": status.last_activity_at},
        )
    if status.reason == "stopped":
        await restart_session_guard()


async def _loop() -> None:
    while True:
        await asyncio.sleep(settings.session_audit.guard_interval_seconds)
        await check_guard()


def start_watchdog() -> None:
    if _dog.task is None or _dog.task.done():
        _dog.task = asyncio.create_task(_loop(), name="session-guard-watchdog")


async def stop_watchdog() -> None:
    task, _dog.task = _dog.task, None
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def reset_watchdog_for_tests() -> None:
    global _dog  # noqa: PLW0603 - one module-level runtime, reset between tests
    _dog = _Watchdog()
