"""Migration 93 — found_by, the search history table, and pending proposals approved."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import _metadata
from core.migration_steps_channel_search import _add_warming_channel_search
from core.migration_steps_warming_channels import _add_warming_channel_labels
from core.migrations import MIGRATIONS
from tests.core.test_migrations_scheduled_posts import _shape

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_LEGACY_DDL = (
    "CREATE TABLE warming_channels ("
    " id INTEGER NOT NULL, channel VARCHAR NOT NULL, label VARCHAR,"
    " created_at VARCHAR NOT NULL, PRIMARY KEY (id), UNIQUE (channel))"
)


@pytest.fixture
def migrated_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("migrated.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(_LEGACY_DDL)
        _add_warming_channel_labels(connection)
        connection.exec_driver_sql(
            "INSERT INTO warming_channels (channel, created_at, source, status)"
            " VALUES ('waiting', 'then', 'discovery', 'pending'),"
            " ('mine', 'then', 'manual', 'approved')",
        )
        _add_warming_channel_search(connection)
    return engine


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_warming_channel_search]
    assert entries == [(93, "add_warming_channel_search", _add_warming_channel_search)]
    versions = [entry[0] for entry in MIGRATIONS]
    assert versions == sorted(versions)


@pytest.mark.parametrize("table", ["warming_channels", "warming_channel_searches"])
def test_created_and_migrated_schemas_match(
    legacy_engine: _EngineFactory, migrated_engine: Engine, table: str
) -> None:
    created = legacy_engine("created.db")
    _metadata.tables[table].create(created)
    assert _shape(created, table) == _shape(migrated_engine, table)


def test_a_waiting_proposal_is_approved_and_nothing_else_moves(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT channel, status, found_by FROM warming_channels ORDER BY id"
        ).all()
    assert [tuple(row) for row in rows] == [
        ("waiting", "approved", None),
        ("mine", "approved", None),
    ]


def test_the_migration_is_idempotent(migrated_engine: Engine) -> None:
    before = _shape(migrated_engine, "warming_channel_searches")
    with migrated_engine.begin() as connection:
        _add_warming_channel_search(connection)
    assert _shape(migrated_engine, "warming_channel_searches") == before


def test_the_migration_runs_without_a_channels_table(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("bare.db")
    with engine.begin() as connection:
        _add_warming_channel_search(connection)
    with engine.connect() as connection:
        assert (
            connection.exec_driver_sql("SELECT COUNT(*) FROM warming_channel_searches").scalar()
            == 0
        )
