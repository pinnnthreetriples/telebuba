"""Adversarial checks on the pre-migration snapshot and the backup schedule (real files)."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from core.config import DbSettings, settings
from core.db import _get_engine, configure_database, dispose_engine  # type: ignore[attr-defined]
from core.db_maintenance import _seconds_until_first_run, run_db_maintenance_loop
from core.db_snapshot import TIMESTAMP_FORMAT, newest_backup_at
from core.migrations import MIGRATIONS

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_NEWEST = MIGRATIONS[-1][0]


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setattr(settings.db, "backup_enabled", True)
    monkeypatch.setattr(settings.db, "backup_dir", tmp_path / "backups")
    path = tmp_path / "telebuba.db"
    configure_database(path)
    yield path
    dispose_engine()


def _tables(path: Path) -> set[str]:
    with closing(sqlite3.connect(path)) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return {r[0] for r in rows}


def _pending_and_missing_table(path: Path) -> str:
    """Make one migration pending AND drop a table that ``create_all`` will recreate."""
    _get_engine()
    dispose_engine()
    victim = "warming_daily_stats"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("DELETE FROM schema_version WHERE version = ?", (_NEWEST,))
        connection.execute("DROP TABLE " + victim)
    configure_database(path)
    return victim


def _put_backup(folder: Path, moment: datetime) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"telebuba-{moment.strftime(TIMESTAMP_FORMAT)}.db"
    target.write_bytes(b"x")
    return target


def test_the_snapshot_precedes_create_all_so_a_new_table_is_not_in_it(
    db_path: Path, tmp_path: Path
) -> None:
    victim = _pending_and_missing_table(db_path)
    _get_engine()
    (snapshot,) = sorted((tmp_path / "backups").glob("telebuba-*.db"))
    assert victim not in _tables(snapshot)  # pre-create_all file
    assert victim in _tables(db_path)  # and create_all did run


def test_concurrent_first_calls_take_exactly_one_snapshot(db_path: Path, tmp_path: Path) -> None:
    _pending_and_missing_table(db_path)
    errors: list[BaseException] = []

    def _go() -> None:
        try:
            _get_engine()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors
    assert len(list((tmp_path / "backups").glob("telebuba-*.db"))) == 1


def test_future_dated_files_must_not_prune_the_snapshot_just_taken(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.db, "backup_keep", 2)
    _pending_and_missing_table(db_path)
    future = datetime.now(UTC) + timedelta(days=3650)
    for i in range(2):
        _put_backup(tmp_path / "backups", future + timedelta(minutes=i))
    _get_engine()
    real = [p for p in (tmp_path / "backups").glob("telebuba-*.db") if p.stat().st_size > 1]
    assert real, "the pre-migration snapshot was pruned away by future-dated files"


def test_a_future_dated_backup_does_not_postpone_the_next_run_beyond_one_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.db, "backup_enabled", True)
    monkeypatch.setattr(settings.db, "backup_dir", tmp_path / "backups")
    monkeypatch.setattr(settings.db, "backup_interval_hours", 24.0)
    _put_backup(tmp_path / "backups", datetime.now(UTC) + timedelta(days=30))
    assert _seconds_until_first_run() <= 24 * 3600


def test_garbage_and_foreign_names_are_ignored_by_the_newest_lookup(tmp_path: Path) -> None:
    folder = tmp_path / "backups"
    old = datetime.now(UTC) - timedelta(hours=5)
    _put_backup(folder, old)
    for name in ("telebuba-garbage.db", "telebuba-.db", "telebuba-zzzz.db", "telebuba-1.db.part"):
        (folder / name).write_bytes(b"x")
    (folder / "other.db").write_bytes(b"x")
    found = newest_backup_at(folder)
    assert found is not None
    assert abs((found - old).total_seconds()) < 1


def test_retry_and_interval_settings_reject_zero() -> None:
    with pytest.raises(ValidationError):
        DbSettings(backup_retry_minutes=0)
    with pytest.raises(ValidationError):
        DbSettings(backup_interval_hours=0)


@pytest.mark.asyncio
async def test_an_unexpected_error_reading_the_backup_dir_does_not_end_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.db, "backup_enabled", True)
    monkeypatch.setattr(settings.db, "backup_dir", tmp_path / "backups")

    def _boom(_dir: object) -> None:
        raise PermissionError

    monkeypatch.setattr("core.db_maintenance.newest_backup_at", _boom)
    task = asyncio.create_task(run_db_maintenance_loop())
    await asyncio.sleep(0.2)
    died = task.done()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert not died, "the maintenance loop task died silently at start"
