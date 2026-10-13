"""Migration 91 — persistent bulk runs, and their parity with ``create_all``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import _metadata
from core.migration_steps_bulk_runs import _add_bulk_runs
from core.migration_steps_outreach import _add_outreach_tables
from core.migrations import MIGRATIONS
from tests.core.test_migrations_scheduled_posts import _shape

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_TABLES = ("bulk_runs", "bulk_run_items", "bulk_run_accounts")


@pytest.fixture
def created_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("created.db")
    _metadata.create_all(engine)
    return engine


@pytest.fixture
def migrated_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("migrated.db")
    with engine.begin() as connection:
        _add_bulk_runs(connection)
    return engine


def _columns(engine: Engine, table: str) -> set[str]:
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(f"PRAGMA table_info({table})").mappings().all()
    return {str(row["name"]) for row in rows}


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_bulk_runs]
    assert entries == [(91, "add_bulk_runs", _add_bulk_runs)]


@pytest.mark.parametrize("table", _TABLES)
def test_created_and_migrated_tables_match(
    created_engine: Engine,
    migrated_engine: Engine,
    table: str,
) -> None:
    assert _shape(created_engine, table) == _shape(migrated_engine, table)


def test_a_saved_limits_row_gains_the_schedule_column(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("limits.db")
    with engine.begin() as connection:
        _add_outreach_tables(connection)
        _add_bulk_runs(connection)
        _add_bulk_runs(connection)
    assert "schedule_json" in _columns(engine, "outreach_limits")


def test_the_migration_is_idempotent(migrated_engine: Engine) -> None:
    before = [_shape(migrated_engine, table) for table in _TABLES]
    with migrated_engine.begin() as connection:
        _add_bulk_runs(connection)
    assert [_shape(migrated_engine, table) for table in _TABLES] == before
