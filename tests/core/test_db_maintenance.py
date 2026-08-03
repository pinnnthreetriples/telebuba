"""Tests for periodic SQLite maintenance + connection-pool sizing (audit #3, #4).

WAL never truncates on its own under a long-lived pool, and ``telebuba.db`` is
the sole datastore (incl. users/auth), so the maintenance task checkpoints the
WAL and, when enabled, writes + prunes a timestamped backup. The pool must be
sized from config so the ``asyncio.to_thread`` executor cannot oversubscribe it.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.pool import QueuePool

from core.config import settings
from core.db import (
    _get_engine,  # type: ignore[attr-defined]
    _vacuum_into,  # type: ignore[attr-defined]
    configure_database,
    run_db_maintenance,
    run_db_maintenance_loop,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path) -> None:
    configure_database(tmp_path / "telebuba.db")


def _fixed_clock(index: int) -> datetime:
    # Distinct per call so successive backups get distinct filenames.
    return datetime(2026, 1, 1, 0, 0, index, tzinfo=UTC)


def test_maintenance_checkpoints_wal_without_backup_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.db, "backup_enabled", False)
    # No engine touched yet — the checkpoint drives engine init and must not raise.
    result = run_db_maintenance()
    assert result is None
    engine = _get_engine()
    with engine.connect() as connection:
        journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
    assert str(journal_mode).lower() == "wal"


def test_maintenance_writes_backup_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(settings.db, "backup_enabled", True)
    monkeypatch.setattr(settings.db, "backup_dir", backup_dir)

    written = run_db_maintenance(clock=lambda: _fixed_clock(1))

    assert written is not None
    assert written.exists()
    assert written.parent == backup_dir
    assert list(backup_dir.glob("telebuba-*.db")) == [written]


def test_maintenance_prunes_to_backup_keep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(settings.db, "backup_enabled", True)
    monkeypatch.setattr(settings.db, "backup_dir", backup_dir)
    # keep > 2 on purpose: with keep=2 the buggy `excess = len - keep` (no
    # max(0, ...)) happens to prune correctly, so that value proves nothing.
    monkeypatch.setattr(settings.db, "backup_keep", 4)

    for index in range(6):
        run_db_maintenance(clock=lambda index=index: _fixed_clock(index))

    remaining = sorted(backup_dir.glob("telebuba-*.db"))
    assert len(remaining) == 4  # oldest two pruned, never more
    # The kept ones are the four most recent (lexicographic == chronological):
    # the clock advanced the seconds field, so 000002..000005 survive over 0/1.
    assert "T000005" in remaining[-1].name
    assert "T000002" in remaining[0].name


def test_partial_backup_never_takes_a_keep_slot_from_a_good_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A half-written backup must not be counted, let alone preferred.

    ``VACUUM INTO`` raises before the pruner runs, so a chronic failure (disk full)
    piles up partial outputs un-pruned. The first later success then keeps the
    newest N — the partials, since their timestamps are newest — and deletes the
    last good backup. With the loop now retrying forever this can run for months,
    ending with a backup directory of nothing restorable.
    """
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(settings.db, "backup_enabled", True)
    monkeypatch.setattr(settings.db, "backup_dir", backup_dir)
    monkeypatch.setattr(settings.db, "backup_keep", 2)

    for index in range(3):
        run_db_maintenance(clock=lambda index=index: _fixed_clock(index))
    good = sorted(backup_dir.glob("telebuba-*.db"))
    assert len(good) == 2

    def _partial_then_enospc(_connection: object, path: Path) -> None:
        # What sqlite leaves behind when the device fills mid-copy.
        path.write_bytes(b"")
        msg = "no space left on device"
        raise OSError(msg)

    monkeypatch.setattr("core.db._vacuum_into", _partial_then_enospc)
    with pytest.raises(OSError, match="no space left"):
        run_db_maintenance(clock=lambda: _fixed_clock(9))
    # The failed run left nothing the pruner will rank as a backup.
    assert sorted(backup_dir.glob("telebuba-*.db")) == good

    monkeypatch.setattr("core.db._vacuum_into", _vacuum_into)
    run_db_maintenance(clock=lambda: _fixed_clock(10))

    remaining = sorted(backup_dir.glob("telebuba-*.db"))
    assert len(remaining) == 2
    assert good[-1] in remaining  # the newest good backup was not pruned for a partial
    assert all(path.stat().st_size > 0 for path in remaining)
    assert list(backup_dir.glob("*.part")) == []  # and the leftover was swept


@pytest.mark.asyncio
async def test_maintenance_loop_cancels_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    # Sleep almost forever so cancellation, not a real interval, ends the task.
    monkeypatch.setattr(settings.db, "backup_interval_hours", 24.0)
    task = asyncio.create_task(run_db_maintenance_loop())
    await asyncio.sleep(0)  # let it reach the first await
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


@pytest.mark.asyncio
async def test_maintenance_loop_survives_a_failed_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raising run must not end maintenance for the rest of the process."""
    # 1e-9h, not 0.0: `backup_interval_hours` is Field(gt=0), so 0.0 is a value
    # monkeypatch can reach but Pydantic forbids — assert on a legal config. It has
    # to be this small, not merely small: 0.001h is 3.6s, and this test waits out
    # two intervals.
    monkeypatch.setattr(settings.db, "backup_interval_hours", 1e-9)
    calls = 0
    ran_again = asyncio.Event()

    def _flaky() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            msg = "no space left on device"
            raise OSError(msg)
        ran_again.set()

    monkeypatch.setattr("core.db.run_db_maintenance", _flaky)
    task = asyncio.create_task(run_db_maintenance_loop())
    try:
        # Only the second call sets the event, so reaching it IS the assertion.
        await asyncio.wait_for(ran_again.wait(), timeout=5)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_maintenance_loop_reports_failure_to_the_operator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed run must reach ``log_event`` — ``logger.exception`` is file-only.

    Without this the loop retries forever with nothing on the Logs page, nothing in
    Sentry and no UI signal, for the sole datastore holding users and auth.
    """
    monkeypatch.setattr(settings.db, "backup_interval_hours", 1e-9)  # legal (gt=0), instant
    events: list[tuple[str, str, dict[str, object] | None]] = []
    reported = asyncio.Event()

    async def _capture(  # signature mirrors log_event; the handler passes no account_id.
        level: str,
        event: str,
        _account_id: str | None = None,
        extra: dict[str, object] | None = None,
    ) -> None:
        events.append((level, event, extra))
        reported.set()

    def _always_fails() -> None:
        msg = "no space left on device"
        raise OSError(msg)

    # core.db imports log_event inside the handler (import cycle), so patching the
    # owning module is what the call site actually resolves.
    monkeypatch.setattr("core.logging.log_event", _capture)
    monkeypatch.setattr("core.db.run_db_maintenance", _always_fails)
    task = asyncio.create_task(run_db_maintenance_loop())
    try:
        await asyncio.wait_for(reported.wait(), timeout=5)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    # Exception type only: no path, no credentials, nothing the log must not carry.
    assert events[0] == ("ERROR", "db_maintenance_failed", {"error": "OSError"})


def test_engine_uses_configured_pool_sizing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Audit #4: pool_size / max_overflow / pool_timeout come from DbSettings."""
    monkeypatch.setattr(settings.db, "pool_size", 7)
    monkeypatch.setattr(settings.db, "max_overflow", 13)
    monkeypatch.setattr(settings.db, "pool_timeout_seconds", 42.0)
    engine = _get_engine()
    pool = engine.pool
    assert isinstance(pool, QueuePool)
    assert pool.size() == 7
    assert pool._max_overflow == 13
    assert pool._timeout == pytest.approx(42.0)
