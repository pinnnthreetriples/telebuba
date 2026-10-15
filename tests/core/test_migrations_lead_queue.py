"""Migration 97 — the lead queue, one live bulk run per kind, and parity with ``create_all``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy.exc import IntegrityError

from core.db import _metadata
from core.migration_steps_bulk_runs import _add_bulk_runs
from core.migration_steps_lead_queue import _add_lead_queue
from core.migrations import MIGRATIONS
from tests.core.test_migrations_scheduled_posts import _shape

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_TABLES = ("lead_queue_items", "lead_queue_settings", "bulk_runs")
_RUN = (
    "INSERT INTO bulk_runs (id, owner_user_id, status, request_json, created_at, updated_at{})"
    " VALUES (?, 'u', 'running', '{{}}', 't', 't'{})"
)


@pytest.fixture
def created_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("created.db")
    _metadata.create_all(engine)
    return engine


@pytest.fixture
def migrated_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("migrated.db")
    with engine.begin() as connection:
        _add_bulk_runs(connection)
        _add_lead_queue(connection)
    return engine


def _index_sql(engine: Engine) -> str:
    with engine.connect() as connection:
        return str(
            connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE name = 'ux_bulk_runs_live'"
            ).scalar()
        )


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_lead_queue]
    assert entries == [(97, "add_lead_queue", _add_lead_queue)]


@pytest.mark.parametrize("table", _TABLES)
def test_created_and_migrated_tables_match(
    created_engine: Engine,
    migrated_engine: Engine,
    table: str,
) -> None:
    assert _shape(created_engine, table) == _shape(migrated_engine, table)


def test_both_spellings_scope_the_live_index_to_running_runs(
    created_engine: Engine, migrated_engine: Engine
) -> None:
    for engine in (created_engine, migrated_engine):
        assert "status = 'running'" in _index_sql(engine)


def test_the_migration_is_idempotent(migrated_engine: Engine) -> None:
    before = [_shape(migrated_engine, table) for table in _TABLES]
    with migrated_engine.begin() as connection:
        _add_lead_queue(connection)
    assert [_shape(migrated_engine, table) for table in _TABLES] == before


def test_an_existing_run_becomes_manual_and_one_live_run_per_kind_is_allowed(
    legacy_engine: _EngineFactory,
) -> None:
    engine = legacy_engine("legacy.db")
    with engine.begin() as connection:
        _add_bulk_runs(connection)
        connection.exec_driver_sql(_RUN.format("", ""), ("old",))
        _add_lead_queue(connection)
        connection.exec_driver_sql(_RUN.format(", kind", ", 'lead_queue'"), ("queue",))
        rows = connection.exec_driver_sql("SELECT id, kind FROM bulk_runs").all()
        kinds = {str(row[0]): str(row[1]) for row in rows}
    assert kinds == {"old": "manual", "queue": "lead_queue"}
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.exec_driver_sql(_RUN.format("", ""), ("second-manual",))
