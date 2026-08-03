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
    monkeypatch.setattr(settings.db, "backup_interval_hours", 0.0)
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
        await asyncio.wait_for(ran_again.wait(), timeout=5)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    assert calls >= 2


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
