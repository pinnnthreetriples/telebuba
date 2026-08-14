"""Single-process neurocomment runtime reset used by test isolation."""

from __future__ import annotations

import asyncio


def _owned_tasks() -> set[asyncio.Task[None]]:
    from services.neurocomment import _runtime  # noqa: PLC0415

    tasks = set(_runtime._TASKS) | set(_runtime._RETIRED_TASKS)  # noqa: SLF001
    for name in (
        "_BACKFILL_TASK",
        "_BACKFILL_TIMER_TASK",
        "_INBOX_RETRY_TASK",
        "_SWEEP_TASK",
        "_SWEEP_STOPPING_TASK",
        "_ONBOARD_TASK",
        "_JOIN_TASK",
    ):
        task = getattr(_runtime, name)
        if task is not None:
            tasks.add(task)
    return tasks


def _cancel_tasks(tasks: set[asyncio.Task[None]]) -> None:
    """Cancel owned tasks only while their event loop can still deliver cancellation."""
    stranded = [task for task in tasks if not task.done() and task.get_loop().is_closed()]
    if stranded:
        msg = "runtime test reset found pending task(s) after their event loop closed"
        raise RuntimeError(msg)
    for task in tasks:
        if not task.done():
            task.cancel()


def _clear_state() -> None:
    from services.neurocomment import _runtime  # noqa: PLC0415

    _runtime._TASKS.clear()  # noqa: SLF001
    _runtime._RETIRED_TASKS.clear()  # noqa: SLF001
    for name in (
        "_BACKFILL_TASK",
        "_BACKFILL_TIMER_TASK",
        "_INBOX_RETRY_TASK",
        "_SWEEP_TASK",
        "_SWEEP_STOPPING_TASK",
        "_ONBOARD_TASK",
        "_JOIN_TASK",
    ):
        setattr(_runtime, name, None)


def reset_for_tests() -> None:
    """Reset quiescent state; live tasks must use :func:`reset_for_tests_async`."""
    from services.neurocomment import _runtime  # noqa: PLC0415

    tasks = _owned_tasks()
    if any(not task.done() for task in tasks):
        _cancel_tasks(tasks)
        msg = "synchronous runtime reset cannot drain pending task(s)"
        raise RuntimeError(msg)
    _clear_state()
    _runtime._JOINED_CHANNELS.clear()  # noqa: SLF001
    _runtime._UNWATCHED_CHANNELS.clear()  # noqa: SLF001
    _runtime._ONBOARD_RERUN = False  # noqa: SLF001
    _runtime._ONBOARD_TASK_OWNER = None  # noqa: SLF001
    _runtime._JOIN_RERUN = False  # noqa: SLF001
    _runtime._JOIN_TASK_OWNER = None  # noqa: SLF001
    _runtime._INBOX_ACCEPTING = True  # noqa: SLF001
    _runtime._INBOX_DISPATCH_LOCK = None  # noqa: SLF001
    _runtime._INBOX_GENERATION = 0  # noqa: SLF001
    _runtime._BACKFILL_GENERATION = 0  # noqa: SLF001
    _runtime._LIFECYCLE_LOCK = None  # noqa: SLF001
    _runtime._LIFECYCLE_OWNER = None  # noqa: SLF001
    _runtime._LIFECYCLE_DEPTH = 0  # noqa: SLF001
    _runtime._RUNTIME_ACCOUNT_ID = None  # noqa: SLF001
    _runtime._RUNTIME_GENERATION = 0  # noqa: SLF001
    _runtime._RUNTIME_OWNER_INITIALIZED = False  # noqa: SLF001
    _runtime._RECONCILE_GENERATION = 0  # noqa: SLF001
    _runtime._SWEEP_RESTART_OWNER = None  # noqa: SLF001


async def reset_for_tests_async() -> None:
    """Cancel, drain, and reset runtime ownership before pytest closes the loop."""
    from services.neurocomment import _runtime  # noqa: PLC0415

    tasks = _owned_tasks()
    live = {task for task in tasks if not task.done()}
    if live:
        current_loop = asyncio.get_running_loop()
        foreign = [task for task in live if task.get_loop() is not current_loop]
        if foreign:
            msg = "runtime test reset found task(s) owned by a different event loop"
            raise RuntimeError(msg)
        # Fence worker finalizers before cancellation. Otherwise ``_run_one`` can observe
        # accepting=True while unwinding and dispatch fresh work after our task snapshot.
        _runtime._INBOX_ACCEPTING = False  # noqa: SLF001
        _runtime._INBOX_GENERATION += 1  # noqa: SLF001
        _runtime._RUNTIME_GENERATION += 1  # noqa: SLF001
        _runtime._RUNTIME_ACCOUNT_ID = None  # noqa: SLF001
        _cancel_tasks(live)
        done, pending = await asyncio.wait(live, timeout=5.0)
        if pending:
            msg = "runtime task(s) ignored cancellation during test reset"
            raise RuntimeError(msg)
        for task in done:
            if not task.cancelled() and (error := task.exception()) is not None:
                raise error
    reset_for_tests()
