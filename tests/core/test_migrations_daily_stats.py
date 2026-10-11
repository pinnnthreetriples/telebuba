"""Migration 75 — the per-day warming totals table, its parity and its log backfill."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.db import _metadata
from core.migration_steps_daily_stats import (
    _add_warming_daily_stats,
    _backfill_warming_daily_stats,
)
from core.migrations import MIGRATIONS

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine

    _EngineFactory = Callable[[str], Engine]

_NOW = datetime(2026, 10, 11, 12, tzinfo=UTC)
_TABLE = "warming_daily_stats"


def _shape(engine: Engine) -> dict[str, object]:
    with engine.connect() as connection:
        run = connection.exec_driver_sql
        columns = run(f"PRAGMA table_info({_TABLE})").mappings().all()
        keys = run(f"PRAGMA foreign_key_list({_TABLE})").mappings().all()
        indexes = {
            row["name"]: (
                row["unique"],
                row["origin"],
                tuple(i["name"] for i in run(f"PRAGMA index_info({row['name']})").mappings()),
            )
            for row in run(f"PRAGMA index_list({_TABLE})").mappings().all()
        }
    return {
        "columns": {
            r["name"]: (r["type"], r["notnull"], r["dflt_value"], r["pk"]) for r in columns
        },
        "keys": list(keys),
        "indexes": indexes,
    }


def _log(engine: Engine, created_at: datetime, extra: object, **row: object) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO logs (created_at, level, status, account_id, event, extra)"
            " VALUES (?, 'INFO', 'ok', ?, ?, ?)",
            (
                created_at.isoformat(),
                row.get("account_id", "acc-1"),
                row.get("event", "warming_cycle_completed"),
                extra if isinstance(extra, str) else json.dumps(extra),
            ),
        )


def _rows(engine: Engine) -> list[tuple[object, ...]]:
    with engine.connect() as connection:
        return [
            tuple(row)
            for row in connection.exec_driver_sql(
                "SELECT account_id, day, cycles, reads, reactions, joins, dms, failures,"
                " floods, phase, trust_score FROM warming_daily_stats ORDER BY account_id, day",
            ).all()
        ]


@pytest.fixture
def created_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("created.db")
    _metadata.create_all(engine)
    return engine


@pytest.fixture
def migrated_engine(legacy_engine: _EngineFactory) -> Engine:
    engine = legacy_engine("migrated.db")
    with engine.begin() as connection:
        _add_warming_daily_stats(connection)
    return engine


def test_the_registry_carries_the_migration_exactly_once() -> None:
    entries = [entry for entry in MIGRATIONS if entry[2] is _add_warming_daily_stats]
    assert entries == [(75, "add_warming_daily_stats", _add_warming_daily_stats)]


def test_created_and_migrated_schemas_match(
    created_engine: Engine,
    migrated_engine: Engine,
) -> None:
    shape = _shape(migrated_engine)
    assert _shape(created_engine) == shape
    # History outlives the account: no foreign key, so no cascade either.
    assert shape["keys"] == []


def test_the_backfill_sums_each_account_day_from_the_cycle_log(created_engine: Engine) -> None:
    today, yesterday = _NOW - timedelta(hours=1), _NOW - timedelta(days=1)
    ok = {"status": "ok", "joined": 1, "reads": 3, "reactions": 2, "messages": 1, "failures": 0}
    flood = {"status": "flood_wait", "reads": 1, "failures": 1}
    _log(created_engine, today, ok)
    _log(created_engine, today, flood)
    _log(created_engine, yesterday, ok)
    _log(created_engine, today, {"status": "peer_flood"}, account_id="acc-2")
    # Not a cycle, outside the window, without an account, or unreadable: all ignored
    # except the unreadable row, which still was a cycle.
    _log(created_engine, today, ok, event="warming_phase_advanced")
    _log(created_engine, _NOW - timedelta(days=31), ok)
    _log(created_engine, today, ok, account_id=None)
    _log(created_engine, today, "not json", account_id="acc-3")

    with created_engine.begin() as connection:
        _backfill_warming_daily_stats(connection, _NOW)

    assert _rows(created_engine) == [
        ("acc-1", "2026-10-10", 1, 3, 2, 1, 1, 0, 0, None, None),
        ("acc-1", "2026-10-11", 2, 4, 2, 1, 1, 1, 1, None, None),
        ("acc-2", "2026-10-11", 1, 0, 0, 0, 0, 0, 1, None, None),
        ("acc-3", "2026-10-11", 1, 0, 0, 0, 0, 0, 0, None, None),
    ]


def test_the_backfill_is_idempotent_and_never_overwrites_a_live_row(
    created_engine: Engine,
) -> None:
    _log(created_engine, _NOW, {"status": "ok", "reads": 3})
    _log(created_engine, _NOW - timedelta(days=1), {"status": "ok", "reads": 5})
    with created_engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO warming_daily_stats (account_id, day, cycles, reads, phase, updated_at)"
            " VALUES ('acc-1', '2026-10-11', 7, 9, 'active', 'now')",
        )

    for _ in range(2):
        with created_engine.begin() as connection:
            _backfill_warming_daily_stats(connection, _NOW)

    assert _rows(created_engine) == [
        ("acc-1", "2026-10-10", 1, 5, 0, 0, 0, 0, 0, None, None),
        ("acc-1", "2026-10-11", 7, 9, 0, 0, 0, 0, 0, "active", None),
    ]


def test_the_migration_runs_without_a_logs_table(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        _add_warming_daily_stats(connection)
    assert _rows(migrated_engine) == []
