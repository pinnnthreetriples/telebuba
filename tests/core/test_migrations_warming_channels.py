"""Migration 80 — warming channel labels, and parity with ``create_all``."""

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
        connection.exec_driver_sql(
            "INSERT INTO warming_channels (channel, created_at) VALUES ('alpha', 'then')",
        )
        _add_warming_channel_labels(connection)
        _add_warming_channel_search(connection)
    return engine


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_warming_channel_labels]
    assert entries == [(80, "add_warming_channel_labels", _add_warming_channel_labels)]


def test_created_and_migrated_schemas_match(
    legacy_engine: _EngineFactory,
    migrated_engine: Engine,
) -> None:
    created = legacy_engine("created.db")
    _metadata.tables["warming_channels"].create(created)
    assert _shape(created, "warming_channels") == _shape(migrated_engine, "warming_channels")


def test_an_existing_row_is_a_manual_approved_channel_of_unknown_language(
    migrated_engine: Engine,
) -> None:
    with migrated_engine.connect() as connection:
        row = (
            connection.exec_driver_sql(
                "SELECT language, country, kind, subscribers, last_post_at, source, status"
                " FROM warming_channels",
            )
            .mappings()
            .one()
        )
    assert dict(row) == {
        "language": None,
        "country": None,
        "kind": None,
        "subscribers": None,
        "last_post_at": None,
        "source": "manual",
        "status": "approved",
    }


def test_the_migration_is_idempotent(migrated_engine: Engine) -> None:
    before = _shape(migrated_engine, "warming_channels")
    with migrated_engine.begin() as connection:
        _add_warming_channel_labels(connection)
    assert _shape(migrated_engine, "warming_channels") == before


def test_a_database_without_the_table_is_left_alone(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("empty.db")
    with engine.begin() as connection:
        _add_warming_channel_labels(connection)
    with engine.connect() as connection:
        tables = connection.exec_driver_sql("SELECT name FROM sqlite_master").all()
    assert tables == []
