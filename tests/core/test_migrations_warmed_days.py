"""Migration 77 — worked warming days: the columns and the backfill from the log."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.db import _metadata
from core.migration_steps_warmed_days import (
    _add_warming_warmed_days,
    _backfill_warmed_days,
    _elapsed_days,
)
from core.migrations import MIGRATIONS

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Connection, Engine

    _EngineFactory = Callable[[str], Engine]

_NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
_COLUMNS = ("warmed_days", "last_warmed_date")


@pytest.fixture
def legacy(legacy_engine: _EngineFactory) -> Engine:
    """A database from before #77: the state table without the two columns."""
    engine = legacy_engine("legacy.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE warming_account_state ("
            " account_id VARCHAR PRIMARY KEY, state VARCHAR NOT NULL,"
            " cycles_completed INTEGER NOT NULL, updated_at VARCHAR NOT NULL,"
            " started_at VARCHAR, stopped_at VARCHAR)",
        )
    _metadata.tables["logs"].create(engine)
    return engine


def _seed_state(
    connection: Connection,
    account_id: str,
    started: datetime,
    *,
    state: str = "sleeping",
    stopped: datetime | None = None,
) -> None:
    connection.exec_driver_sql(
        "INSERT INTO warming_account_state"
        " (account_id, state, cycles_completed, updated_at, started_at, stopped_at)"
        " VALUES (?, ?, 0, ?, ?, ?)",
        (
            account_id,
            state,
            _NOW.isoformat(),
            started.isoformat(),
            stopped.isoformat() if stopped else None,
        ),
    )


def _log_cycle(connection: Connection, account_id: str, at: datetime, **extra: object) -> None:
    connection.exec_driver_sql(
        "INSERT INTO logs (created_at, level, status, account_id, event, extra)"
        " VALUES (?, 'INFO', 'ok', ?, 'warming_cycle_completed', ?)",
        (at.isoformat(), account_id, json.dumps({"status": "ok", **extra})),
    )


def _row(engine: Engine, account_id: str) -> tuple[int, str | None]:
    with engine.connect() as connection:
        row = connection.exec_driver_sql(
            "SELECT warmed_days, last_warmed_date FROM warming_account_state WHERE account_id = ?",
            (account_id,),
        ).one()
    return int(row[0]), row[1]


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_warming_warmed_days]
    assert entries == [(77, "add_warming_warmed_days", _add_warming_warmed_days)]


def test_migrated_columns_match_create_all(legacy: Engine, legacy_engine: _EngineFactory) -> None:
    with legacy.begin() as connection:
        _add_warming_warmed_days(connection)
    created = legacy_engine("created.db")
    _metadata.tables["accounts"].create(created)
    _metadata.tables["warming_account_state"].create(created)

    def _columns(engine: Engine) -> dict[str, tuple[object, ...]]:
        with engine.connect() as connection:
            rows = connection.exec_driver_sql("PRAGMA table_info(warming_account_state)")
            return {
                r["name"]: (r["type"], r["notnull"], r["dflt_value"])
                for r in rows.mappings()
                if r["name"] in _COLUMNS
            }

    assert _columns(legacy) == _columns(created)
    assert set(_columns(legacy)) == set(_COLUMNS)


def test_backfill_counts_distinct_worked_dates_since_the_stint_began(legacy: Engine) -> None:
    started = _NOW - timedelta(days=6)
    with legacy.begin() as connection:
        _seed_state(connection, "acc-1", started)
        _log_cycle(connection, "acc-1", started - timedelta(days=2))  # an earlier stint
        _log_cycle(connection, "acc-1", started + timedelta(days=1))
        _log_cycle(connection, "acc-1", started + timedelta(days=1, hours=2))  # same date
        _log_cycle(connection, "acc-1", started + timedelta(days=3), status="flood_wait")
        _log_cycle(connection, "acc-1", started + timedelta(days=4), status="failed")
        _log_cycle(connection, "acc-1", started + timedelta(days=5), status="failed", reads=2)
        _add_warming_warmed_days(connection)

    assert _row(legacy, "acc-1") == (2, (started + timedelta(days=5)).date().isoformat())


def test_backfill_never_exceeds_the_calendar_days_elapsed(legacy: Engine) -> None:
    started = _NOW - timedelta(days=1, hours=13)  # one whole day, three dates touched
    with legacy.begin() as connection:
        _add_warming_warmed_days(connection)
        _seed_state(connection, "acc-1", started)
        for hours in (0, 24, 36):
            _log_cycle(connection, "acc-1", started + timedelta(hours=hours))
        _backfill_warmed_days(connection, _NOW)

    assert _row(legacy, "acc-1")[0] == 1


def test_a_stopped_stint_is_capped_at_its_stop(legacy: Engine) -> None:
    started = _NOW - timedelta(days=10)
    with legacy.begin() as connection:
        _add_warming_warmed_days(connection)
        _seed_state(connection, "acc-1", started, state="idle", stopped=started + timedelta(days=2))
        for day in range(5):
            _log_cycle(connection, "acc-1", started + timedelta(days=day))
        _backfill_warmed_days(connection, _NOW)

    assert _row(legacy, "acc-1")[0] == 2


def test_backfill_leaves_rows_without_work_and_already_counted_rows(legacy: Engine) -> None:
    started = _NOW - timedelta(days=5)
    with legacy.begin() as connection:
        _add_warming_warmed_days(connection)
        _seed_state(connection, "quiet", started)
        _seed_state(connection, "counted", started)
        connection.exec_driver_sql(
            "UPDATE warming_account_state SET warmed_days = 4, last_warmed_date = '2026-10-11'"
            " WHERE account_id = 'counted'",
        )
        _log_cycle(connection, "counted", started + timedelta(days=1))
        _backfill_warmed_days(connection, _NOW)

    assert _row(legacy, "quiet") == (0, None)
    assert _row(legacy, "counted") == (4, "2026-10-11")


def test_the_migration_is_idempotent(legacy: Engine) -> None:
    started = _NOW - timedelta(days=3)
    with legacy.begin() as connection:
        _seed_state(connection, "acc-1", started)
        _log_cycle(connection, "acc-1", started + timedelta(days=1))
        _add_warming_warmed_days(connection)
        _add_warming_warmed_days(connection)

    assert _row(legacy, "acc-1")[0] == 1


def test_a_database_without_the_state_table_is_left_alone(legacy_engine: _EngineFactory) -> None:
    engine = legacy_engine("empty.db")
    with engine.begin() as connection:
        _add_warming_warmed_days(connection)
        tables = connection.exec_driver_sql("SELECT name FROM sqlite_master").all()
    assert tables == []


def test_elapsed_days_reads_naive_stamps_as_utc_and_skips_garbage() -> None:
    assert _elapsed_days("2026-10-10T12:00:00", None, _NOW) == 2
    assert _elapsed_days("not a date", None, _NOW) is None


def test_backfill_skips_a_row_with_an_unreadable_start(legacy: Engine) -> None:
    with legacy.begin() as connection:
        _add_warming_warmed_days(connection)
        connection.exec_driver_sql(
            "INSERT INTO warming_account_state"
            " (account_id, state, cycles_completed, updated_at, started_at)"
            " VALUES ('acc-1', 'sleeping', 0, 'x', 'garbage')",
        )
        _backfill_warmed_days(connection, _NOW)

    assert _row(legacy, "acc-1") == (0, None)
