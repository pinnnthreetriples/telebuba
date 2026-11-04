"""Migration 104 — the session audit tables, and their parity with ``create_all``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import _metadata
from core.migration_steps_session_audits import _add_account_session_audits
from core.migrations import MIGRATIONS
from tests.core.test_migrations_scheduled_posts import _shape

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_TABLES = ("account_session_audits", "account_session_audit_events")


@pytest.fixture
def created_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("created.db")
    _metadata.create_all(engine)
    return engine


@pytest.fixture
def migrated_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("migrated.db")
    with engine.begin() as connection:
        _add_account_session_audits(connection)
    return engine


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_account_session_audits]
    assert entries == [(104, "add_account_session_audits", _add_account_session_audits)]


@pytest.mark.parametrize("table", _TABLES)
def test_created_and_migrated_tables_match(
    created_engine: Engine, migrated_engine: Engine, table: str
) -> None:
    assert _shape(created_engine, table) == _shape(migrated_engine, table)


def test_the_migration_is_idempotent(migrated_engine: Engine) -> None:
    before = [_shape(migrated_engine, table) for table in _TABLES]
    with migrated_engine.begin() as connection:
        _add_account_session_audits(connection)
    assert [_shape(migrated_engine, table) for table in _TABLES] == before
