"""Migration 107 — the ``account_rekeys`` table, and its parity with ``create_all``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import _metadata
from core.migration_steps_rekeys import _add_account_rekeys
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
        _add_account_rekeys(connection)
    return engine


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_account_rekeys]
    assert entries == [(107, "add_account_rekeys", _add_account_rekeys)]


def test_created_and_migrated_tables_match(created_engine: Engine, migrated_engine: Engine) -> None:
    assert _shape(created_engine, "account_rekeys") == _shape(migrated_engine, "account_rekeys")


def test_the_migration_is_idempotent(migrated_engine: Engine) -> None:
    before = _shape(migrated_engine, "account_rekeys")
    with migrated_engine.begin() as connection:
        _add_account_rekeys(connection)
    assert _shape(migrated_engine, "account_rekeys") == before
