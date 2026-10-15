"""Migration #92 — ``warming_settings.tuning_preset`` / ``tuning_custom`` for the pace card."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core._schema_tables import _warming_settings
from core.migration_steps_tuning import _add_warming_settings_tuning
from core.migrations import MIGRATIONS

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_STEP = _add_warming_settings_tuning
_COLUMNS = {"tuning_preset", "tuning_custom"}


def _columns(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        rows = connection.exec_driver_sql("PRAGMA table_info(warming_settings)").mappings().all()
    return {str(row["name"]) for row in rows}


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _STEP]
    assert entries == [(92, "add_warming_settings_tuning", _STEP)]
    versions = [entry[0] for entry in MIGRATIONS]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))


def test_a_legacy_row_reads_as_normal_and_the_step_is_idempotent(
    legacy_engine: _EngineFactory,
) -> None:
    engine = legacy_engine("legacy.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE warming_settings (id INTEGER PRIMARY KEY, updated_at VARCHAR NOT NULL)",
        )
        connection.exec_driver_sql(
            "INSERT INTO warming_settings (id, updated_at) VALUES (1, '2026-01-01T00:00:00+00:00')",
        )
        _STEP(connection)
        _STEP(connection)
        stored = connection.exec_driver_sql(
            "SELECT tuning_preset, tuning_custom FROM warming_settings"
        ).one()

    assert tuple(stored) == (None, None)
    assert _columns(engine) >= _COLUMNS
    assert {column.name for column in _warming_settings.columns} >= _COLUMNS


def test_the_step_skips_a_database_without_the_table(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("empty.db")
    with engine.begin() as connection:
        _STEP(connection)

    assert _columns(engine) == set()
