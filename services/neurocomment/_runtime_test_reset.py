"""Single-process neurocomment runtime reset used by test isolation."""

from __future__ import annotations


def reset_for_tests() -> None:
    """Cancel and clear every runtime-owned handle between event-loop tests."""
    from services.neurocomment import _runtime  # noqa: PLC0415

    _runtime._TASKS.clear()  # noqa: SLF001
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
    _runtime._BACKFILL_AT.clear()  # noqa: SLF001
    _runtime._LIFECYCLE_LOCK = None  # noqa: SLF001
    _runtime._LIFECYCLE_OWNER = None  # noqa: SLF001
    _runtime._LIFECYCLE_DEPTH = 0  # noqa: SLF001
    _runtime._RUNTIME_ACCOUNT_ID = None  # noqa: SLF001
    _runtime._RUNTIME_GENERATION = 0  # noqa: SLF001
    _runtime._RUNTIME_OWNER_INITIALIZED = False  # noqa: SLF001
    _runtime._RECONCILE_GENERATION = 0  # noqa: SLF001
    for task in tuple(_runtime._RETIRED_TASKS):  # noqa: SLF001
        task.cancel()
    _runtime._RETIRED_TASKS.clear()  # noqa: SLF001
    for name in (
        "_BACKFILL_TASK",
        "_BACKFILL_TIMER_TASK",
        "_INBOX_RETRY_TASK",
        "_SWEEP_TASK",
        "_ONBOARD_TASK",
        "_JOIN_TASK",
    ):
        task = getattr(_runtime, name)
        if task is not None:
            task.cancel()
            setattr(_runtime, name, None)
    _runtime._SWEEP_STOPPING_TASK = None  # noqa: SLF001
