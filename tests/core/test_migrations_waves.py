"""Migrations 93-94: the warming wave, the confirmed spam block, the ``warming_stopped`` kind.

93 adds ``wave`` (backfilled from the journal's current life) and ``spam_blocked_at``,
and moves rows the old three-strikes ladder parked into ``spam_blocked``. 94 rebuilds
the journal so its CHECK admits ``warming_stopped``, carrying every row and id.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy.exc import IntegrityError

from core.db import _metadata
from core.migration_steps_lifecycle import _add_account_lifecycle_events
from core.migration_steps_waves import _add_warming_state_wave, _allow_lifecycle_warming_stopped
from core.migrations import MIGRATIONS
from tests.core.test_migrations_scheduled_posts import _shape

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_JOURNAL = "account_lifecycle_events"
# warming_account_state as it stood before 93 (only the columns these tests touch).
_STATE_V92 = (
    "CREATE TABLE warming_account_state ("
    " account_id VARCHAR PRIMARY KEY, state VARCHAR NOT NULL,"
    " last_event VARCHAR, started_at VARCHAR, updated_at VARCHAR NOT NULL)"
)


def _sql(engine: Engine, statement: str, *args: object) -> list[tuple[object, ...]]:
    with engine.begin() as connection:
        result = connection.exec_driver_sql(statement, args)
        return [tuple(row) for row in result.all()] if result.returns_rows else []


def _journal(engine: Engine, account_id: str, kind: str, created_at: str, stint: str) -> None:
    _sql(
        engine,
        "INSERT INTO account_lifecycle_events (account_id, kind, created_at, stint_started_at)"
        " VALUES (?, ?, ?, ?)",
        account_id,
        kind,
        created_at,
        stint,
    )


@pytest.fixture
def v92_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("v92.db")
    _metadata.tables["accounts"].create(engine)
    _metadata.tables["logs"].create(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(_STATE_V92)
        _add_account_lifecycle_events(connection)
    for account_id in ("a1", "a2", "a3"):
        _sql(
            engine,
            "INSERT INTO accounts (account_id, status, created_at, updated_at)"
            " VALUES (?, 'alive', '2026-09-01', '2026-09-01')",
            account_id,
        )
        _sql(
            engine,
            "INSERT INTO warming_account_state (account_id, state, updated_at)"
            " VALUES (?, 'idle', '2026-10-01T00:00:00+00:00')",
            account_id,
        )
    return engine


def _migrate(engine: Engine) -> None:
    with engine.begin() as connection:
        _add_warming_state_wave(connection)
        _allow_lifecycle_warming_stopped(connection)


def test_the_registry_carries_both_migrations_once_in_order() -> None:
    versions = [(v, body) for v, _name, body in MIGRATIONS if v in {93, 94}]
    assert versions == [(93, _add_warming_state_wave), (94, _allow_lifecycle_warming_stopped)]
    numbers = [entry[0] for entry in MIGRATIONS]
    assert numbers == sorted(numbers)


def test_the_wave_is_backfilled_from_the_current_life(v92_engine: Engine) -> None:
    engine = v92_engine
    # a1: two stints, a restart that carried the first one's clock in between.
    _journal(engine, "a1", "warming_started", "2026-09-02", "2026-09-02")
    _journal(engine, "a1", "warming_started", "2026-09-03", "2026-09-02")
    _journal(engine, "a1", "warming_started", "2026-09-20", "2026-09-20")
    # a2: a deleted earlier life with two stints, then one in the re-imported life.
    _journal(engine, "a2", "warming_started", "2026-09-02", "2026-09-02")
    _journal(engine, "a2", "warming_started", "2026-09-05", "2026-09-05")
    _journal(engine, "a2", "deleted", "2026-09-06", "2026-09-05")
    _journal(engine, "a2", "warming_started", "2026-09-10", "2026-09-10")
    # a3: never warmed.

    _migrate(engine)

    rows = _sql(engine, "SELECT account_id, wave FROM warming_account_state")
    waves = {str(row[0]): row[1] for row in rows}
    assert waves == {"a1": 2, "a2": 1, "a3": 1}


def test_a_row_the_old_ladder_parked_becomes_spam_blocked(v92_engine: Engine) -> None:
    engine = v92_engine
    _sql(
        engine,
        "UPDATE warming_account_state SET state = 'error', last_event = ? WHERE account_id = ?",
        "quarantine_exhausted",
        "a1",
    )
    _sql(
        engine,
        "UPDATE warming_account_state SET state = 'error', last_event = ? WHERE account_id = ?",
        "loop_crashed",
        "a2",
    )

    _migrate(engine)

    rows = _sql(
        engine,
        "SELECT account_id, state, spam_blocked_at FROM warming_account_state ORDER BY 1",
    )
    assert rows == [
        ("a1", "spam_blocked", "2026-10-01T00:00:00+00:00"),
        ("a2", "error", None),
        ("a3", "idle", None),
    ]


def test_the_journal_rebuild_keeps_rows_and_ids_and_admits_the_new_kind(
    v92_engine: Engine,
) -> None:
    engine = v92_engine
    _journal(engine, "a1", "warming_started", "2026-09-02", "2026-09-02")
    _journal(engine, "a1", "promoted", "2026-09-16", "2026-09-02")
    before = _sql(engine, "SELECT * FROM account_lifecycle_events ORDER BY id")
    with pytest.raises(IntegrityError):
        _journal(engine, "a1", "warming_stopped", "2026-09-17", "2026-09-02")

    _migrate(engine)

    assert _sql(engine, "SELECT * FROM account_lifecycle_events ORDER BY id") == before
    _journal(engine, "a1", "warming_stopped", "2026-09-17", "2026-09-02")
    (row,) = _sql(engine, "SELECT id FROM account_lifecycle_events WHERE kind = 'warming_stopped'")
    assert row[0] == int(str(before[-1][0])) + 1
    with pytest.raises(IntegrityError):
        _journal(engine, "a1", "nonsense", "2026-09-18", "2026-09-02")
    assert set(_shape(engine, _JOURNAL)["indexes"]) == {  # ty: ignore[invalid-argument-type]
        "ix_account_lifecycle_events_account",
        "ix_account_lifecycle_events_kind",
        "ux_account_lifecycle_events_source_log",
    }


def test_a_second_run_changes_nothing(v92_engine: Engine) -> None:
    engine = v92_engine
    _journal(engine, "a1", "warming_started", "2026-09-02", "2026-09-02")
    _migrate(engine)
    state = _sql(engine, "SELECT * FROM warming_account_state ORDER BY 1")
    journal = _sql(engine, "SELECT * FROM account_lifecycle_events ORDER BY id")
    schema = _shape(engine, _JOURNAL)

    _migrate(engine)

    assert _sql(engine, "SELECT * FROM warming_account_state ORDER BY 1") == state
    assert _sql(engine, "SELECT * FROM account_lifecycle_events ORDER BY id") == journal
    assert _shape(engine, _JOURNAL) == schema


def test_a_database_without_the_tables_is_left_alone(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("bare.db")
    _migrate(engine)
    assert _sql(engine, "SELECT name FROM sqlite_master WHERE type = 'table'") == []
