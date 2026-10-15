"""Migration 97 — recipient history and re-checks, and their parity with ``create_all``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import _metadata
from core.migration_steps_outreach import _add_outreach_tables
from core.migration_steps_recipients import _add_recipient_history
from core.migrations import MIGRATIONS
from tests.core.test_migrations_scheduled_posts import _shape

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_TABLES = ("recipient_history", "contact_rechecks")


@pytest.fixture
def created_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("created.db")
    _metadata.create_all(engine)
    return engine


@pytest.fixture
def migrated_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("migrated.db")
    with engine.begin() as connection:
        _add_recipient_history(connection)
    return engine


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_recipient_history]
    assert entries == [(97, "add_recipient_history", _add_recipient_history)]


@pytest.mark.parametrize("table", _TABLES)
def test_created_and_migrated_tables_match(
    created_engine: Engine,
    migrated_engine: Engine,
    table: str,
) -> None:
    assert _shape(created_engine, table) == _shape(migrated_engine, table)


def test_a_saved_limits_row_gains_the_rules_column(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("limits.db")
    with engine.begin() as connection:
        _add_outreach_tables(connection)
        _add_recipient_history(connection)
        _add_recipient_history(connection)
        columns = connection.exec_driver_sql("PRAGMA table_info(outreach_limits)").mappings()
        assert "recipients_json" in {str(row["name"]) for row in columns}


def test_the_migration_is_idempotent(migrated_engine: Engine) -> None:
    before = [_shape(migrated_engine, table) for table in _TABLES]
    with migrated_engine.begin() as connection:
        _add_recipient_history(connection)
    assert [_shape(migrated_engine, table) for table in _TABLES] == before
