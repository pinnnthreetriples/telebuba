"""When the maintenance loop runs: on start if overdue, after a failure soon, not a day later.

``run_db_maintenance`` is patched to a counter: the schedule is what is under test. The
backup directory is a real temp dir holding real file names, since "the newest backup" is
read from there.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db_maintenance import _seconds_until_first_run, run_db_maintenance_loop
from core.db_snapshot import TIMESTAMP_FORMAT

if TYPE_CHECKING:
    from pathlib import Path

_DAY_HOURS = 24.0


@pytest.fixture(autouse=True)
def backups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(settings.db, "backup_enabled", True)
    monkeypatch.setattr(settings.db, "backup_dir", backup_dir)
    monkeypatch.setattr(settings.db, "backup_interval_hours", _DAY_HOURS)
    monkeypatch.setattr(settings.db, "backup_retry_minutes", 1e-9)
    return backup_dir


def _backup_file(backup_dir: Path, taken: datetime) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / f"telebuba-{taken.strftime(TIMESTAMP_FORMAT)}.db"
    path.write_bytes(b"x")
    return path


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    monkeypatch.setattr("core.db_maintenance.run_db_maintenance", lambda: calls.append(1))
    return calls


async def _stop(task: asyncio.Task[None]) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def _wait_for(calls: list[int], count: int) -> None:
    async with asyncio.timeout(5):
        while len(calls) < count:  # noqa: ASYNC110 - a worker thread appends to the list
            await asyncio.sleep(0.01)


def test_no_backup_at_all_is_due_now() -> None:
    assert _seconds_until_first_run() == 0.0


def test_a_backup_older_than_the_interval_is_due_now(backups: Path) -> None:
    _backup_file(backups, datetime.now(UTC) - timedelta(hours=_DAY_HOURS + 1))
    assert _seconds_until_first_run() == 0.0


def test_a_fresh_backup_waits_out_the_rest_of_the_interval(backups: Path) -> None:
    _backup_file(backups, datetime.now(UTC) - timedelta(hours=2))
    wait = _seconds_until_first_run()
    assert 21.9 * 3600 < wait <= 22 * 3600


def test_backups_off_waits_a_whole_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.db, "backup_enabled", False)
    assert _seconds_until_first_run() == _DAY_HOURS * 3600


@pytest.mark.asyncio
async def test_the_loop_backs_up_at_once_when_none_exists(runs: list[int]) -> None:
    """A process that restarts more often than the interval used to never back up."""
    task = asyncio.create_task(run_db_maintenance_loop())
    try:
        await _wait_for(runs, 1)
    finally:
        await _stop(task)


@pytest.mark.asyncio
async def test_the_loop_waits_when_the_newest_backup_is_fresh(
    backups: Path, runs: list[int]
) -> None:
    _backup_file(backups, datetime.now(UTC) - timedelta(minutes=5))
    task = asyncio.create_task(run_db_maintenance_loop())
    try:
        await asyncio.sleep(0.3)
        assert runs == []
    finally:
        await _stop(task)


@pytest.mark.asyncio
async def test_a_failed_run_is_retried_after_the_retry_delay_not_a_day_later(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def _fails_once() -> None:
        calls.append(1)
        if len(calls) == 1:
            msg = "no space left on device"
            raise OSError(msg)

    async def _no_log(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr("core.db_maintenance.run_db_maintenance", _fails_once)
    monkeypatch.setattr("core.logging.log_event", _no_log)
    task = asyncio.create_task(run_db_maintenance_loop())
    try:
        await _wait_for(calls, 2)  # the interval is 24 h: only the retry delay can get here
    finally:
        await _stop(task)


@pytest.mark.asyncio
async def test_after_a_success_the_next_run_waits_a_whole_interval(runs: list[int]) -> None:
    task = asyncio.create_task(run_db_maintenance_loop())
    try:
        await _wait_for(runs, 1)
        await asyncio.sleep(0.3)  # the retry delay is ~0: only a wrong delay runs again
        assert runs == [1]
    finally:
        await _stop(task)
