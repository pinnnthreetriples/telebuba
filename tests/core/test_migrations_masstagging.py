"""Migration 73 — the masstagging schema, and its parity with ``create_all``.

A fresh database is built by ``core.repositories.masstagging._tables`` and an existing
one by the migration; neither path exercises the other, so the two spellings are
compared here down to CHECK constraints, keys and indexes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import core.repositories.masstagging._tables  # noqa: F401 - registers the tables
from core.db import _metadata
from core.migration_steps_masstagging import _add_masstagging_tables
from core.migrations import MIGRATIONS
from tests.core.test_migrations_scheduled_posts import _checks, _shape

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_TABLES = (
    "masstagging_campaigns",
    "masstagging_settings",
    "masstagging_accounts",
    "masstagging_targets",
    "masstagging_journal",
    "masstagging_tagged_registry",
    "masstagging_account_day",
)


@pytest.fixture
def created_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("created.db")
    _metadata.create_all(engine)
    return engine


@pytest.fixture
def migrated_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("migrated.db")
    _metadata.tables["accounts"].create(engine)
    with engine.begin() as connection:
        _add_masstagging_tables(connection)
    return engine


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_masstagging_tables]
    assert entries == [(73, "add_masstagging_tables", _add_masstagging_tables)]


@pytest.mark.parametrize("table", _TABLES)
def test_created_and_migrated_schemas_match(
    created_engine: Engine,
    migrated_engine: Engine,
    table: str,
) -> None:
    assert _shape(created_engine, table) == _shape(migrated_engine, table)


def test_the_migration_is_idempotent(migrated_engine: Engine) -> None:
    before = {table: _shape(migrated_engine, table) for table in _TABLES}
    with migrated_engine.begin() as connection:
        _add_masstagging_tables(connection)
    assert {table: _shape(migrated_engine, table) for table in _TABLES} == before


def test_target_states_are_checked(created_engine: Engine) -> None:
    checks = _checks(created_engine, "masstagging_targets")
    assert any("'flood'" in check and "'primed'" in check for check in checks)
