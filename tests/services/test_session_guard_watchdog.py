"""The guard's heartbeat, and the watchdog that reports (once) and repairs a stalled guard."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from services import session_guard
from services.session_guard import _loop, _watchdog

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def events(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[str, object]]]:
    _loop.reset_for_tests()
    _watchdog.reset_watchdog_for_tests()
    logged: list[tuple[str, object]] = []

    async def _log(_level: str, event: str, **kwargs: object) -> None:
        logged.append((event, kwargs.get("extra")))

    monkeypatch.setattr(_watchdog, "log_event", _log)
    yield logged
    _loop.reset_for_tests()
    _watchdog.reset_watchdog_for_tests()


@pytest.mark.asyncio
async def test_a_guard_that_never_started_is_not_a_stall() -> None:
    status = await session_guard.read_session_guard_status()

    assert (status.running, status.stalled) == (False, False)


@pytest.mark.asyncio
async def test_a_silent_guard_is_reported_once_until_it_recovers(
    monkeypatch: pytest.MonkeyPatch, events: list[tuple[str, object]]
) -> None:
    async def _forever() -> None:
        await asyncio.Event().wait()

    running = asyncio.create_task(_forever())
    _loop._state.task = running
    _loop._beat()
    monkeypatch.setattr(_loop, "silence_limit_seconds", lambda: -1.0)

    await _watchdog.check_guard()
    await _watchdog.check_guard()

    assert [event for event, _extra in events] == ["session_guard_stalled"]
    assert events[0][1]["reason"] == "silent"  # ty: ignore[not-subscriptable]

    monkeypatch.setattr(_loop, "silence_limit_seconds", lambda: 3600.0)
    await _watchdog.check_guard()  # healthy again: the next stall is news
    monkeypatch.setattr(_loop, "silence_limit_seconds", lambda: -1.0)
    await _watchdog.check_guard()

    assert [event for event, _extra in events] == ["session_guard_stalled"] * 2
    running.cancel()


@pytest.mark.asyncio
async def test_a_guard_task_that_ended_is_reported_and_started_again(
    monkeypatch: pytest.MonkeyPatch, events: list[tuple[str, object]]
) -> None:
    async def _ended() -> None:
        return None

    ended = asyncio.create_task(_ended())
    await ended
    _loop._state.task = ended
    started: list[bool] = []

    async def _start() -> None:
        started.append(True)

    monkeypatch.setattr(_loop, "start_session_guard", _start)

    assert (await session_guard.read_session_guard_status()).reason == "stopped"
    await _watchdog.check_guard()

    assert [event for event, _extra in events] == ["session_guard_stalled"]
    assert started == [True]


@pytest.mark.asyncio
async def test_a_working_guard_beats_and_shutdown_stops_the_watchdog_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    passes = asyncio.Event()

    async def _pass() -> int:
        passes.set()
        return 0

    async def _no_watch() -> None:
        return None

    monkeypatch.setattr(_loop, "run_guard_pass", _pass)
    monkeypatch.setattr(session_guard, "start_session_death_watch", _no_watch)
    monkeypatch.setattr(session_guard, "shutdown_session_death_watch", _no_watch)

    await session_guard.start_session_guard()
    await asyncio.wait_for(passes.wait(), timeout=5)
    status = await session_guard.read_session_guard_status()
    dog = _watchdog._dog.task
    await session_guard.shutdown_session_guard()

    assert (status.running, status.stalled) == (True, False)
    assert status.last_activity_at is not None
    assert dog is not None
    assert dog.done()
