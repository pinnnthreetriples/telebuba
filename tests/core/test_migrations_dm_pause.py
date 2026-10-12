"""Migration #78 — ``warming_settings.dm_paused_until`` for the fleet DM breaker."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core._schema_tables import _warming_settings
from core.migration_steps_dm_pause import _add_warming_settings_dm_paused_until
from core.migrations import MIGRATIONS

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_STEP = _add_warming_settings_dm_paused_until


def _columns(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        rows = connection.exec_driver_sql("PRAGMA table_info(warming_settings)").mappings().all()
    return {str(row["name"]) for row in rows}


def _create_legacy_settings(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE warming_settings (id INTEGER PRIMARY KEY, updated_at VARCHAR NOT NULL)",
        )
        connection.exec_driver_sql(
            "INSERT INTO warming_settings (id, updated_at) VALUES (1, '2026-01-01T00:00:00+00:00')",
        )


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _STEP]
    assert entries == [(78, "add_warming_settings_dm_paused_until", _STEP)]
    versions = [entry[0] for entry in MIGRATIONS]
    assert versions == sorted(versions)


def test_a_legacy_row_gains_a_null_pause_and_the_step_is_idempotent(
    legacy_engine: _EngineFactory,
) -> None:
    engine = legacy_engine("legacy.db")
    _create_legacy_settings(engine)
    with engine.begin() as connection:
        _STEP(connection)
        _STEP(connection)
        stored = connection.exec_driver_sql("SELECT dm_paused_until FROM warming_settings").one()

    assert tuple(stored) == (None,)
    assert "dm_paused_until" in _columns(engine)
    assert "dm_paused_until" in {column.name for column in _warming_settings.columns}


def test_the_step_skips_a_database_without_the_table(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("empty.db")
    with engine.begin() as connection:
        _STEP(connection)

    assert _columns(engine) == set()
