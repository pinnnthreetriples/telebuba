"""In-memory state of the runs in flight: their tasks and their live progress.

Mirrors ``services.neurocomment._discovery_state``: the handles live in the module that
owns them. The row in ``user_parser_runs`` is the durable half; what is here dies with
the process, and ``reconcile_user_parser_on_startup`` marks a run it orphaned as
``interrupted``.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from schemas.user_parser_run import UserParserRun


@dataclass(slots=True)
class LiveRun:
    """A run while it collects: the read model the modal polls, and its task."""

    run: UserParserRun
    task: asyncio.Task[None] | None = None
    # Set by Stop, so the cancelled run settles as ``stopped`` rather than ``interrupted``.
    stop_requested: bool = False


_RUNS: dict[str, LiveRun] = {}


def live(run_id: str) -> LiveRun | None:
    return _RUNS.get(run_id)


def spawn(run: UserParserRun, coro: Coroutine[None, None, None]) -> None:
    """Register the run and start its task; it forgets itself once done."""
    entry = LiveRun(run=run)
    _RUNS[run.run_id] = entry
    task = asyncio.create_task(coro)
    entry.task = task
    task.add_done_callback(lambda _done: _RUNS.pop(run.run_id, None))


def request_stop(run_id: str) -> bool:
    """Cancel a run in flight; whatever it collected is still saved. ``False`` if none."""
    entry = _RUNS.get(run_id)
    if entry is None or entry.task is None or entry.task.done():
        return False
    entry.stop_requested = True
    entry.task.cancel()
    return True


async def wait_settled(run_id: str) -> None:
    """Wait for a stopping run to write its last state, so a read after Stop sees it."""
    entry = _RUNS.get(run_id)
    if entry is not None and entry.task is not None:
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(entry.task)


async def shutdown() -> None:
    """Cancel every run and await it: each saves what it has as ``interrupted``."""
    tasks = [entry.task for entry in _RUNS.values() if entry.task is not None]
    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    _RUNS.clear()


def reset_for_tests() -> None:
    for entry in _RUNS.values():
        if entry.task is not None:
            entry.task.cancel()
    _RUNS.clear()
