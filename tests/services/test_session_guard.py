"""The session guard: one background task, each step guarded, stopped cleanly."""

from __future__ import annotations

import asyncio

import pytest

from core.config import settings
from services import session_guard
from services.session_guard import _loop


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    _loop.reset_for_tests()
    # A developer's .env may configure a real vault; the pass's health step must not ask it.
    monkeypatch.setattr(settings.passkey_vault, "token", "")

    # The start-up file look has its own tests (``test_session_guard_file_check``); here it
    # would only race the tests' tiny step deadline with real database reads.
    async def _no_file_look() -> None:
        return None

    monkeypatch.setattr(_loop, "run_file_reconciliation", _no_file_look)

    # Likewise the key-intent repair (``test_key_repair``): a real read of the journal.
    async def _no_key_repair() -> int:
        return 0

    monkeypatch.setattr(_loop, "repair_key_intents", _no_key_repair)

    # And the link re-check (``test_link_status``): a real list of the accounts.
    async def _no_recheck() -> None:
        return None

    monkeypatch.setattr(_loop, "run_recheck_pass", _no_recheck)

    # The proxy watch and the stray-pod stop have their own tests (``test_proxy_watch``).
    async def _no_proxy_watch() -> str | None:
        return None

    async def _no_pods_stopped() -> int:
        return 0

    monkeypatch.setattr(_loop, "run_proxy_watch_pass", _no_proxy_watch)
    monkeypatch.setattr(_loop, "stop_pods_off_proxy", _no_pods_stopped)


def _steps(
    monkeypatch: pytest.MonkeyPatch, *, old_keys: int | BaseException, watched: str | None
) -> None:
    async def _due() -> int:
        if isinstance(old_keys, BaseException):
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


def _capture_events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, object]]:
    events: list[tuple[str, str, object]] = []

    async def _log(level: str, event: str, **kwargs: object) -> None:
        events.append((level, event, kwargs.get("extra")))

    monkeypatch.setattr(_loop, "log_event", _log)
    return events


@pytest.mark.asyncio
async def test_a_foreign_cancelled_error_is_a_step_failure_and_the_next_step_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = _capture_events(monkeypatch)
    _steps(monkeypatch, old_keys=asyncio.CancelledError(), watched="acc-1")

    assert await session_guard.run_guard_pass() == 1
    assert events == [
        ("WARNING", "session_guard_failed", {"step": "old_keys", "error_type": "CancelledError"})
    ]


@pytest.mark.asyncio
async def test_a_step_past_its_deadline_is_logged_and_the_pass_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = _capture_events(monkeypatch)
    released = asyncio.Event()

    async def _hang() -> int:
        try:
            await asyncio.sleep(3600)
        finally:
            released.set()  # the cancellation unwound the step (and any lock it held)
        return 0

    _steps(monkeypatch, old_keys=0, watched="acc-1")
    monkeypatch.setattr(_loop, "close_due_old_keys", _hang)
    monkeypatch.setattr(_loop.settings.session_audit, "guard_step_timeout_seconds", 0.05)

    assert await asyncio.wait_for(session_guard.run_guard_pass(), timeout=5) == 1
    assert released.is_set()
    assert events == [
        ("WARNING", "session_guard_failed", {"step": "old_keys", "error_type": "TimeoutError"})
    ]


@pytest.mark.asyncio
async def test_the_loop_survives_an_escape_and_shutdown_still_cancels_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = _capture_events(monkeypatch)
    calls = 0
    second = asyncio.Event()

    async def _pass() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise asyncio.CancelledError  # a stray one that escaped the step guard
        second.set()
        return 0

    monkeypatch.setattr(_loop, "run_guard_pass", _pass)
    monkeypatch.setattr(_loop.settings.session_audit, "guard_interval_seconds", 0.01)

    await session_guard.start_session_guard()
    await asyncio.wait_for(second.wait(), timeout=5)
    task = _loop._state.task
    assert task is not None
    await asyncio.wait_for(session_guard.shutdown_session_guard(), timeout=5)

    assert task.done()
    assert events[0] == (
        "WARNING",
        "session_guard_failed",
        {"step": "loop", "error_type": "CancelledError"},
    )


@pytest.mark.asyncio
async def test_shutdown_cancels_a_step_in_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    started = asyncio.Event()

    async def _hang() -> int:
        started.set()
        await asyncio.sleep(3600)
        return 0

    _steps(monkeypatch, old_keys=0, watched=None)
    monkeypatch.setattr(_loop, "close_due_old_keys", _hang)

    await session_guard.start_session_guard()
    await asyncio.wait_for(started.wait(), timeout=5)
    task = _loop._state.task
    assert task is not None
    await asyncio.wait_for(session_guard.shutdown_session_guard(), timeout=5)

    assert task.done()


@pytest.mark.asyncio
async def test_the_proxy_watch_and_the_stray_pod_stop_run_each_pass_and_failures_stay_logged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _steps(monkeypatch, old_keys=0, watched=None)
    ran: list[str] = []
    events = _capture_events(monkeypatch)

    async def _proxy() -> str | None:
        ran.append("proxy_watch")
        msg = "boom"
        raise RuntimeError(msg)

    async def _pods() -> int:
        ran.append("pods_off_proxy")
        return 2

    monkeypatch.setattr(_loop, "run_proxy_watch_pass", _proxy)
    monkeypatch.setattr(_loop, "stop_pods_off_proxy", _pods)

    await _loop.run_guard_pass()

    assert ran == ["proxy_watch", "pods_off_proxy"]
    assert [(e[1], e[2]) for e in events if e[1] == "session_guard_failed"] == [
        ("session_guard_failed", {"step": "proxy_watch", "error_type": "RuntimeError"})
    ]
