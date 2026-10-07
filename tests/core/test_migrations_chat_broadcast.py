"""Migrations 68/69 — the chat-broadcast schema, and its parity with ``create_all``.

A fresh database is built by ``core.repositories.chat_broadcast._tables`` and an existing
one by the migration; neither path exercises the other, so the two spellings are compared
here down to CHECK constraints, keys and indexes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

import core.repositories.chat_broadcast._tables  # noqa: F401 - registers the tables
from core.db import _metadata
from core.migration_steps_chat_broadcast import (
    _add_chat_broadcast_ignore_deleted,
    _add_chat_broadcast_tables,
)
from core.migrations import MIGRATIONS
from tests.core.test_migrations_scheduled_posts import _checks, _shape

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_TABLES = (
    "chat_broadcast_campaigns",
    "chat_broadcast_accounts",
    "chat_broadcast_targets",
    "chat_broadcast_messages",
    "chat_broadcast_events",
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
        _add_chat_broadcast_tables(connection)
        _add_chat_broadcast_ignore_deleted(connection)
    return engine


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_chat_broadcast_tables]
    assert entries == [(68, "add_chat_broadcast_tables", _add_chat_broadcast_tables)]
    kept = [entry for entry in MIGRATIONS if entry[2] is _add_chat_broadcast_ignore_deleted]
    assert kept == [(69, "add_chat_broadcast_ignore_deleted", _add_chat_broadcast_ignore_deleted)]


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
        _add_chat_broadcast_tables(connection)
        _add_chat_broadcast_ignore_deleted(connection)
    assert {table: _shape(migrated_engine, table) for table in _TABLES} == before


def test_the_journal_key_is_unique_per_step(created_engine: Engine) -> None:
    indexes = cast(
        "dict[str, object]", _shape(created_engine, "chat_broadcast_messages")["indexes"]
    )
    key = ("run_id", "chat_key", "round", "step_index")
    assert indexes["ux_cb_messages_step"] == (1, "c", key)
    checks = _checks(created_engine, "chat_broadcast_messages")
    assert any("'unconfirmed'" in check for check in checks)
