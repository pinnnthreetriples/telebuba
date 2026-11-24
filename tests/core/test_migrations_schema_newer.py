"""An older build must not run on a database a newer build migrated."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import _get_engine, configure_database, dispose_engine  # type: ignore[attr-defined]
from core.db_snapshot import prepare_schema
from core.migrations import (
    MIGRATIONS,
    SchemaNewerThanCodeError,
    apply_migrations,
    ensure_schema_not_newer,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine

_NEWEST = max(version for version, _n, _b in MIGRATIONS)


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path) -> Iterator[None]:
    configure_database(tmp_path / "x.db")
    yield
    dispose_engine()


def _engine(_tmp_path: Path) -> Engine:
    return _get_engine()  # a fully built database: tables and every migration stamped


def _stamp(tmp_path: Path, version: int) -> None:
    with closing(sqlite3.connect(tmp_path / "x.db")) as connection, connection:
        connection.execute(
            "INSERT OR REPLACE INTO schema_version(version, name, applied_at) VALUES (?, 'x', 'x')",
            (version,),
        )


def test_a_newer_database_is_refused_before_anything_is_applied(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _stamp(tmp_path, _NEWEST + 5)
    with closing(sqlite3.connect(tmp_path / "x.db")) as connection, connection:
        connection.execute("DELETE FROM schema_version WHERE version = ?", (_NEWEST,))
    with pytest.raises(SchemaNewerThanCodeError) as caught:
        apply_migrations(engine)
    assert str(_NEWEST + 5) in str(caught.value)
    assert str(_NEWEST) in str(caught.value)
    assert "backups folder" in str(caught.value)
    with closing(sqlite3.connect(tmp_path / "x.db")) as connection:
        stamped = {row[0] for row in connection.execute("SELECT version FROM schema_version")}
    assert _NEWEST not in stamped  # nothing was applied on top


def test_equal_and_older_databases_proceed(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    ensure_schema_not_newer(engine)  # equal
    with closing(sqlite3.connect(tmp_path / "x.db")) as connection, connection:
        connection.execute("DELETE FROM schema_version WHERE version = ?", (_NEWEST,))
    apply_migrations(engine)  # older: catches up
    ensure_schema_not_newer(engine)


def test_a_file_without_stamps_proceeds(tmp_path: Path) -> None:
    dispose_engine()
    configure_database(tmp_path / "empty.db")
    ensure_schema_not_newer(_get_engine())


def test_an_unknown_version_between_known_ones_is_not_newer(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    known = {version for version, _n, _b in MIGRATIONS}
    gap = next(v for v in range(1, _NEWEST) if v not in known) if len(known) < _NEWEST else None
    if gap is None:  # the registry has no hole: fake one by removing a stamp instead
        with closing(sqlite3.connect(tmp_path / "x.db")) as connection, connection:
            connection.execute("DELETE FROM schema_version WHERE version = 3")
    else:
        _stamp(tmp_path, gap)
    ensure_schema_not_newer(engine)
    apply_migrations(engine)


def test_prepare_schema_fails_loudly_and_disposes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.db, "backup_dir", tmp_path / "backups")
    engine = _engine(tmp_path)
    _stamp(tmp_path, _NEWEST + 1)
    created: list[bool] = []
    with pytest.raises(SchemaNewerThanCodeError):
        prepare_schema(engine, lambda _e: created.append(True))
    assert created == []
