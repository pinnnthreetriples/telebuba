"""Migration 76 — flood memory, and its parity with ``create_all``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import _metadata
from core.migration_steps_flood_memory import _add_warming_flood_memory
from core.migrations import MIGRATIONS
from tests.core.test_migrations_scheduled_posts import _shape

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]


@pytest.fixture
def created_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("created.db")
    _metadata.create_all(engine)
    return engine


@pytest.fixture
def migrated_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("migrated.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE warming_account_state (account_id VARCHAR PRIMARY KEY)",
        )
        _add_warming_flood_memory(connection)
    return engine


def _columns(engine: Engine, table: str) -> set[str]:
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(f"PRAGMA table_info({table})").mappings().all()
    return {str(row["name"]) for row in rows}


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_warming_flood_memory]
    assert entries == [(76, "add_warming_flood_memory", _add_warming_flood_memory)]


def test_created_and_migrated_flood_tables_match(
    created_engine: Engine,
    migrated_engine: Engine,
) -> None:
    table = "warming_flood_events"
    assert _shape(created_engine, table) == _shape(migrated_engine, table)


def test_the_state_row_gains_recovered_at(migrated_engine: Engine) -> None:
    assert "recovered_at" in _columns(migrated_engine, "warming_account_state")


def test_the_migration_is_idempotent(migrated_engine: Engine) -> None:
    before = _shape(migrated_engine, "warming_flood_events")
    with migrated_engine.begin() as connection:
        _add_warming_flood_memory(connection)
    assert _shape(migrated_engine, "warming_flood_events") == before


def test_a_database_without_warming_state_still_gets_the_table(
    legacy_engine: _EngineFactory,
) -> None:
    engine = legacy_engine("bare.db")
    with engine.begin() as connection:
        _add_warming_flood_memory(connection)
    assert "account_id" in _columns(engine, "warming_flood_events")
