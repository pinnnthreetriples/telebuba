"""Migration 123 — the ``pending_cleanups`` table, and its parity with ``create_all``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import _metadata
from core.migration_steps_pending_cleanups import _add_pending_cleanups
from core.migrations import MIGRATIONS
from tests.core.test_migrations_scheduled_posts import _shape

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_TABLE = "pending_cleanups"


@pytest.fixture
def created_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("created.db")
    _metadata.create_all(engine)
    return engine


@pytest.fixture
def migrated_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("migrated.db")
    with engine.begin() as connection:
        _add_pending_cleanups(connection)
    return engine


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_pending_cleanups]
    assert entries == [(123, "add_pending_cleanups", _add_pending_cleanups)]


def test_created_and_migrated_tables_match(created_engine: Engine, migrated_engine: Engine) -> None:
    assert _shape(created_engine, _TABLE) == _shape(migrated_engine, _TABLE)


def test_the_migration_is_idempotent(migrated_engine: Engine) -> None:
    before = _shape(migrated_engine, _TABLE)
    with migrated_engine.begin() as connection:
        _add_pending_cleanups(connection)
    assert _shape(migrated_engine, _TABLE) == before
