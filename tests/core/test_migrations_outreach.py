"""Migrations 89, 90 and 98 — outreach tables, and their parity with ``create_all``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import _metadata
from core.migration_steps_bulk_runs import _add_bulk_runs
from core.migration_steps_outreach import _add_outreach_presets, _add_outreach_tables
from core.migration_steps_outreach_protection import _add_outreach_protection
from core.migrations import MIGRATIONS
from tests.core.test_migrations_scheduled_posts import _shape

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_TABLES = ("outreach_limits", "outreach_daily_usage", "outreach_presets", "outreach_account_rests")


@pytest.fixture
def created_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("created.db")
    _metadata.create_all(engine)
    return engine


@pytest.fixture
def migrated_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("migrated.db")
    with engine.begin() as connection:
        _add_outreach_tables(connection)
        _add_outreach_presets(connection)
        # 91 adds the limits row's schedule column, 98 its protection column.
        _add_bulk_runs(connection)
        _add_outreach_protection(connection)
    return engine


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_outreach_tables]
    assert entries == [(89, "add_outreach_tables", _add_outreach_tables)]
    presets = [entry for entry in MIGRATIONS if entry[2] is _add_outreach_presets]
    assert presets == [(90, "add_outreach_presets", _add_outreach_presets)]
    protection = [entry for entry in MIGRATIONS if entry[2] is _add_outreach_protection]
    assert protection == [(98, "add_outreach_protection", _add_outreach_protection)]


@pytest.mark.parametrize("table", _TABLES)
def test_created_and_migrated_tables_match(
    created_engine: Engine,
    migrated_engine: Engine,
    table: str,
) -> None:
    assert _shape(created_engine, table) == _shape(migrated_engine, table)


def test_the_migration_is_idempotent(migrated_engine: Engine) -> None:
    before = [_shape(migrated_engine, table) for table in _TABLES]
    with migrated_engine.begin() as connection:
        _add_outreach_tables(connection)
        _add_outreach_presets(connection)
        _add_outreach_protection(connection)
    assert [_shape(migrated_engine, table) for table in _TABLES] == before
