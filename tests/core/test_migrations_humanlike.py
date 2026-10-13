"""Migrations 85-87 (Прогрев 2.0, stage 4) and their parity with ``create_all``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import _metadata
from core.migration_steps_humanlike import (
    _add_account_registered_at,
    _add_warming_account_character,
    _add_warming_state_visit,
)
from core.migrations import MIGRATIONS
from tests.core.test_migrations_scheduled_posts import _shape

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_STEPS = (_add_account_registered_at, _add_warming_account_character, _add_warming_state_visit)
_COLUMNS = {
    "accounts": {"telegram_registered_at", "registered_source"},
    "warming_account_state": {"visit_kind", "cycle_plan"},
}


def _columns(engine: Engine, table: str) -> dict[str, str]:
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(f"PRAGMA table_info({table})").mappings()
        return {str(row["name"]): str(row["type"]) for row in rows}


@pytest.fixture
def migrated_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("migrated.db")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE accounts (account_id VARCHAR PRIMARY KEY)")
        connection.exec_driver_sql(
            "CREATE TABLE warming_account_state (account_id VARCHAR PRIMARY KEY)",
        )
        for step in _STEPS:
            step(connection)
    return engine


def test_the_registry_carries_each_step_once_in_order() -> None:
    entries = [(number, step) for number, _name, step in MIGRATIONS if step in _STEPS]
    assert entries == list(zip((85, 86, 87), _STEPS, strict=True))


def test_the_migrated_schema_matches_create_all(
    legacy_engine: _EngineFactory,
    migrated_engine: Engine,
) -> None:
    created = legacy_engine("created.db")
    _metadata.create_all(created)
    for table, names in _COLUMNS.items():
        fresh = {k: v for k, v in _columns(created, table).items() if k in names}
        assert fresh == {k: v for k, v in _columns(migrated_engine, table).items() if k in names}
        assert set(fresh) == names
    assert _shape(created, "warming_account_character") == _shape(
        migrated_engine, "warming_account_character"
    )


def test_the_steps_are_idempotent(migrated_engine: Engine) -> None:
    before = {t: _shape(migrated_engine, t) for t in (*_COLUMNS, "warming_account_character")}
    with migrated_engine.begin() as connection:
        for step in _STEPS:
            step(connection)
    after = {t: _shape(migrated_engine, t) for t in (*_COLUMNS, "warming_account_character")}
    assert after == before
