"""Migrations 82-84 — the warming operator-console columns, and their parity with ``create_all``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import _metadata
from core.migration_steps_console import (
    _add_warming_state_cycle_base,
    _add_warming_state_geo_ack,
    _add_warming_state_nc_start,
)
from core.migrations import MIGRATIONS
from tests.core.test_migrations_scheduled_posts import _shape

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_STEPS = (_add_warming_state_geo_ack, _add_warming_state_cycle_base, _add_warming_state_nc_start)
_COLUMNS = {"geo_ack", "cycle_base_actions", "nc_start_mode", "nc_handoff_at"}


def _columns(engine: Engine) -> dict[str, str]:
    with engine.connect() as connection:
        rows = connection.exec_driver_sql("PRAGMA table_info(warming_account_state)").mappings()
        return {str(row["name"]): str(row["type"]) for row in rows}


@pytest.fixture
def migrated_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("migrated.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE warming_account_state (account_id VARCHAR PRIMARY KEY)",
        )
        for step in _STEPS:
            step(connection)
    return engine


def test_the_registry_carries_each_step_once_in_order() -> None:
    entries = [(number, step) for number, _name, step in MIGRATIONS if step in _STEPS]
    assert entries == list(zip((82, 83, 84), _STEPS, strict=True))


def test_the_migrated_columns_match_create_all(
    legacy_engine: _EngineFactory,
    migrated_engine: Engine,
) -> None:
    created = legacy_engine("created.db")
    _metadata.create_all(created)

    fresh = {name: kind for name, kind in _columns(created).items() if name in _COLUMNS}
    assert fresh == {
        name: kind for name, kind in _columns(migrated_engine).items() if name in _COLUMNS
    }
    assert set(fresh) == _COLUMNS


def test_the_steps_are_idempotent(migrated_engine: Engine) -> None:
    before = _shape(migrated_engine, "warming_account_state")
    with migrated_engine.begin() as connection:
        for step in _STEPS:
            step(connection)
    assert _shape(migrated_engine, "warming_account_state") == before


def test_a_database_without_the_state_table_is_left_alone(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("bare.db")
    with engine.begin() as connection:
        for step in _STEPS:
            step(connection)
        tables = connection.exec_driver_sql("SELECT name FROM sqlite_master").all()
    assert tables == []
