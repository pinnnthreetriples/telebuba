"""A snapshot before schema migrations, and an engine that is published only once it is ready.

Real SQLite files in ``tmp_path``. A database is made "pending" by un-stamping its newest
migration row, which is exactly what an upgrade finds: an existing file, one migration behind.
"""

from __future__ import annotations

import sqlite3
import stat
import sys
from contextlib import closing
from typing import TYPE_CHECKING

import pytest

import core.db as core_db
from core.config import settings
from core.db import _get_engine, configure_database, dispose_engine  # type: ignore[attr-defined]
from core.migrations import MIGRATIONS, apply_migrations

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine
    from sqlalchemy.pool import Pool

_NEWEST = MIGRATIONS[-1][0]


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setattr(settings.db, "backup_enabled", True)
    monkeypatch.setattr(settings.db, "backup_dir", tmp_path / "backups")
    path = tmp_path / "telebuba.db"
    configure_database(path)
    yield path
    dispose_engine()


def _stamps(path: Path) -> set[int]:
    with closing(sqlite3.connect(path)) as connection:
        return {row[0] for row in connection.execute("SELECT version FROM schema_version")}


def _make_one_migration_pending(path: Path) -> None:
    _get_engine()
    dispose_engine()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("DELETE FROM schema_version WHERE version = ?", (_NEWEST,))
    configure_database(path)


def _snapshots(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "backups").glob("telebuba-*.db"))


def test_an_existing_database_with_a_pending_migration_is_snapshotted_first(
    db_path: Path, tmp_path: Path
) -> None:
    _make_one_migration_pending(db_path)

    _get_engine()

    (snapshot,) = _snapshots(tmp_path)
    assert _NEWEST not in _stamps(snapshot)  # taken BEFORE the migration ran
    assert _NEWEST in _stamps(db_path)  # and the migration did run
    assert not list((tmp_path / "backups").glob("*.part"))
    if sys.platform != "win32":
        assert stat.S_IMODE(snapshot.stat().st_mode) == 0o600


@pytest.mark.usefixtures("db_path")
def test_a_fresh_database_takes_no_snapshot(tmp_path: Path) -> None:
    _get_engine()
    assert _snapshots(tmp_path) == []


def test_nothing_pending_takes_no_snapshot(db_path: Path, tmp_path: Path) -> None:
    _get_engine()
    dispose_engine()
    configure_database(db_path)

    _get_engine()

    assert _snapshots(tmp_path) == []


def test_backups_off_migrates_without_a_snapshot(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_one_migration_pending(db_path)
    monkeypatch.setattr(settings.db, "backup_enabled", False)

    _get_engine()

    assert _snapshots(tmp_path) == []
    assert _NEWEST in _stamps(db_path)


def test_a_snapshot_that_cannot_be_written_stops_the_migration_and_is_retried(
    db_path: Path, tmp_path: Path
) -> None:
    _make_one_migration_pending(db_path)
    (tmp_path / "backups").write_bytes(b"a file where the directory should be")

    with pytest.raises(OSError):  # noqa: PT011 - the exact errno differs per platform
        _get_engine()

    assert core_db._state.engine is None
    assert _NEWEST not in _stamps(db_path)  # fail closed: the credential store is untouched

    (tmp_path / "backups").unlink()
    _get_engine()  # the next call starts from scratch

    assert len(_snapshots(tmp_path)) == 1
    assert _NEWEST in _stamps(db_path)


@pytest.mark.usefixtures("db_path")
def test_a_failed_migration_disposes_the_engine_and_the_next_call_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[tuple[Engine, Pool]] = []

    def _fails_once(engine: Engine) -> None:
        attempts.append((engine, engine.pool))
        if len(attempts) == 1:
            msg = "migration blew up"
            raise RuntimeError(msg)
        apply_migrations(engine)

    monkeypatch.setattr("core.db_snapshot.apply_migrations", _fails_once)

    with pytest.raises(RuntimeError, match="blew up"):
        _get_engine()

    failed, pool = attempts[0]
    assert failed.pool is not pool  # dispose() replaced the pool: the engine was released
    assert core_db._state.engine is None

    engine = _get_engine()  # retried from scratch, not served the broken engine

    assert engine is not failed
    assert core_db._state.engine is engine
    assert len(attempts) == 2
