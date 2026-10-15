"""Migration 96: the journal's CHECK admits ``spam_block_lifted``, every row and id kept."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy.exc import IntegrityError

from core.migration_steps_lifecycle import _add_account_lifecycle_events
from core.migration_steps_spam_lift import _allow_lifecycle_spam_block_lifted
from core.migration_steps_waves import _allow_lifecycle_warming_stopped
from core.migrations import MIGRATIONS
from tests.core.test_migrations_scheduled_posts import _shape

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_JOURNAL = "account_lifecycle_events"


def _sql(engine: Engine, statement: str, *args: object) -> list[tuple[object, ...]]:
    with engine.begin() as connection:
        result = connection.exec_driver_sql(statement, args)
        return [tuple(row) for row in result.all()] if result.returns_rows else []


def _journal(engine: Engine, kind: str, created_at: str) -> None:
    _sql(
        engine,
        "INSERT INTO account_lifecycle_events (account_id, kind, created_at) VALUES ('a1', ?, ?)",
        kind,
        created_at,
    )


@pytest.fixture
def v95_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("v95.db")
    with engine.begin() as connection:
        _add_account_lifecycle_events(connection)
        _allow_lifecycle_warming_stopped(connection)
    return engine


def _migrate(engine: Engine) -> None:
    with engine.begin() as connection:
        _allow_lifecycle_spam_block_lifted(connection)


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _allow_lifecycle_spam_block_lifted]
    assert [entry[0] for entry in entries] == [96]


def test_the_rebuild_keeps_rows_and_ids_and_admits_the_new_kind(v95_engine: Engine) -> None:
    engine = v95_engine
    _journal(engine, "warming_started", "2026-09-02")
    _journal(engine, "warming_stopped", "2026-09-10")
    before = _sql(engine, "SELECT * FROM account_lifecycle_events ORDER BY id")
    with pytest.raises(IntegrityError):
        _journal(engine, "spam_block_lifted", "2026-09-20")

    _migrate(engine)

    assert _sql(engine, "SELECT * FROM account_lifecycle_events ORDER BY id") == before
    _journal(engine, "spam_block_lifted", "2026-09-20")
    (row,) = _sql(
        engine, "SELECT id FROM account_lifecycle_events WHERE kind = 'spam_block_lifted'"
    )
    assert row[0] == int(str(before[-1][0])) + 1
    with pytest.raises(IntegrityError):
        _journal(engine, "nonsense", "2026-09-21")
    assert set(_shape(engine, _JOURNAL)["indexes"]) == {  # ty: ignore[invalid-argument-type]
        "ix_account_lifecycle_events_account",
        "ix_account_lifecycle_events_kind",
        "ux_account_lifecycle_events_source_log",
    }


def test_a_second_run_changes_nothing(v95_engine: Engine) -> None:
    engine = v95_engine
    _journal(engine, "warming_started", "2026-09-02")
    _migrate(engine)
    journal = _sql(engine, "SELECT * FROM account_lifecycle_events ORDER BY id")
    schema = _shape(engine, _JOURNAL)

    _migrate(engine)

    assert _sql(engine, "SELECT * FROM account_lifecycle_events ORDER BY id") == journal
    assert _shape(engine, _JOURNAL) == schema


def test_a_database_without_the_journal_is_left_alone(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("bare.db")
    _migrate(engine)
    assert _sql(engine, "SELECT name FROM sqlite_master WHERE type = 'table'") == []
