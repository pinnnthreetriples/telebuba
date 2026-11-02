"""The session guard: one background task, each step guarded, stopped cleanly."""

from __future__ import annotations

import asyncio

import pytest

from services import session_guard
from services.session_guard import _loop


@pytest.fixture(autouse=True)
def _fresh() -> None:
    _loop.reset_for_tests()


@pytest.mark.asyncio
async def test_a_pass_returns_how_many_old_keys_it_ended(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _due() -> int:
        return 3

    monkeypatch.setattr(_loop, "close_due_old_keys", _due)

    assert await session_guard.run_guard_pass() == 3


@pytest.mark.asyncio
async def test_a_failing_step_is_logged_and_never_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, str, object]] = []

    async def _due() -> int:
        raise RuntimeError

    async def _log(level: str, event: str, **kwargs: object) -> None:
        events.append((level, event, kwargs.get("extra")))

    monkeypatch.setattr(_loop, "close_due_old_keys", _due)
    monkeypatch.setattr(_loop, "log_event", _log)

    assert await session_guard.run_guard_pass() == 0
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

    monkeypatch.setattr(_loop, "close_due_old_keys", _due)
    monkeypatch.setattr(_loop.settings.session_audit, "rekey_poll_seconds", 3600.0)

    await session_guard.start_session_guard()
    await session_guard.start_session_guard()  # already running: a no-op
    await asyncio.wait_for(ran.wait(), timeout=5)
    await session_guard.shutdown_session_guard()
    await session_guard.shutdown_session_guard()  # nothing running: a no-op

    assert passes == 1
