"""Migration 72 — the user-parser schema, and its parity with ``create_all``.

A fresh database is built by ``core.repositories.user_parser._tables`` and an existing
one by the migration; neither path exercises the other, so the two spellings are compared
here down to keys, indexes and CHECK constraints.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy.exc import IntegrityError

import core.repositories.user_parser._tables  # noqa: F401 - registers the tables
from core.db import _metadata
from core.migration_steps_user_parser import _add_user_parser_tables
from core.migrations import MIGRATIONS
from tests.core.test_migrations_scheduled_posts import _shape

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_TABLES = ("user_parser_runs", "user_parser_users", "user_parser_presets")


@pytest.fixture
def created_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("created.db")
    _metadata.create_all(engine)
    return engine


@pytest.fixture
def migrated_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("migrated.db")
    with engine.begin() as connection:
        _add_user_parser_tables(connection)
    return engine


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_user_parser_tables]
    assert entries == [(72, "add_user_parser_tables", _add_user_parser_tables)]
    assert max(entry[0] for entry in MIGRATIONS) == 72


@pytest.mark.parametrize("table", _TABLES)
def test_created_and_migrated_schemas_match(
    created_engine: Engine, migrated_engine: Engine, table: str
) -> None:
    assert _shape(created_engine, table) == _shape(migrated_engine, table)


def test_the_migration_is_idempotent(migrated_engine: Engine) -> None:
    before = {table: _shape(migrated_engine, table) for table in _TABLES}
    with migrated_engine.begin() as connection:
        _add_user_parser_tables(connection)
    assert {table: _shape(migrated_engine, table) for table in _TABLES} == before


def test_people_cascade_from_their_run(created_engine: Engine) -> None:
    keys = _shape(created_engine, "user_parser_users")["keys"]
    assert keys == [("user_parser_runs", "run_id", "run_id", "CASCADE")]


def test_an_unknown_status_is_refused(migrated_engine: Engine) -> None:
    with pytest.raises(IntegrityError), migrated_engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO user_parser_runs (run_id, name, created_at, status, mode,"
            " request_json, account_ids_json)"
            " VALUES ('r', 'n', 't', 'bogus', 'members', '{}', '[]')"
        )
