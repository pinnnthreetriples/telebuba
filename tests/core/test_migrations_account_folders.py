"""Migration 71 — the account-folder schema, and its parity with ``create_all``.

A fresh database is built by ``core.repositories.account_folders._tables`` and an
existing one by the migration; neither path exercises the other, so the two spellings
are compared here down to keys and indexes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import core.repositories.account_folders._tables  # noqa: F401 - registers the tables
from core.db import _metadata
from core.migration_steps_account_folders import _add_account_folders
from core.migrations import MIGRATIONS
from tests.core.test_migrations_scheduled_posts import _shape

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_TABLES = ("account_folders", "account_folder_members")


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
        _add_account_folders(connection)
    return engine


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_account_folders]
    assert entries == [(71, "add_account_folders", _add_account_folders)]


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
        _add_account_folders(connection)
    assert {table: _shape(migrated_engine, table) for table in _TABLES} == before


def test_a_membership_cascades_from_its_folder_only(created_engine: Engine) -> None:
    keys = _shape(created_engine, "account_folder_members")["keys"]
    assert keys == [
        ("account_folders", "folder_id", "folder_id", "CASCADE"),
        ("accounts", "account_id", "account_id", "NO ACTION"),
    ]
