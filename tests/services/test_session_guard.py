"""The session guard: one background task, each step guarded, stopped cleanly."""

from __future__ import annotations

import asyncio

import pytest

from services import session_guard
from services.session_guard import _loop


@pytest.fixture(autouse=True)
def _fresh() -> None:
    _loop.reset_for_tests()


def _steps(
    monkeypatch: pytest.MonkeyPatch, *, old_keys: int | Exception, watched: str | None
) -> None:
    async def _due() -> int:
        if isinstance(old_keys, Exception):
            raise old_keys
        return old_keys

    async def _watch() -> str | None:
        return watched

    monkeypatch.setattr(_loop, "close_due_old_keys", _due)
    monkeypatch.setattr(_loop, "run_watch_pass", _watch)


@pytest.mark.asyncio
async def test_a_pass_counts_the_old_keys_ended_and_the_account_watched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _steps(monkeypatch, old_keys=3, watched="acc-1")

    assert await session_guard.run_guard_pass() == 4


@pytest.mark.asyncio
async def test_a_failing_step_is_logged_and_the_other_still_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, str, object]] = []

    async def _log(level: str, event: str, **kwargs: object) -> None:
        events.append((level, event, kwargs.get("extra")))

    _steps(monkeypatch, old_keys=RuntimeError(), watched="acc-1")
    monkeypatch.setattr(_loop, "log_event", _log)

    assert await session_guard.run_guard_pass() == 1
    assert events == [
        ("WARNING", "session_guard_failed", {"step": "old_keys", "error_type": "RuntimeError"})
    ]


@pytest.mark.asyncio
async def test_the_task_starts_once_runs_a_pass_and_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    passes = 0
    ran = asyncio.Event()

    async def _due() -> int:
        nonlocal passes
        passes += 1
        ran.set()
        return 0

    _steps(monkeypatch, old_keys=0, watched=None)
    monkeypatch.setattr(_loop, "close_due_old_keys", _due)
    monkeypatch.setattr(_loop.settings.session_audit, "guard_interval_seconds", 3600.0)

    await session_guard.start_session_guard()
    await session_guard.start_session_guard()  # already running: a no-op
    await asyncio.wait_for(ran.wait(), timeout=5)
    await session_guard.shutdown_session_guard()
    await session_guard.shutdown_session_guard()  # nothing running: a no-op

    assert passes == 1
