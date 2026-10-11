"""Migration 75 — the per-day warming totals table, seeded from the activity log.

``CREATE ... IF NOT EXISTS``: ``create_all`` runs BEFORE ``apply_migrations``, so on
a fresh database the table comes from ``core._schema_warming_daily_stats`` and only
the backfill does anything. The two spellings must agree — pinned by
``tests/core/test_migrations_daily_stats.py``.

The backfill reads the last ``_BACKFILL_DAYS`` of ``warming_cycle_completed`` rows,
the only history the log keeps by default. It never overwrites a row that already
exists, so running it twice adds nothing. Days are the UTC date of the log row;
phase and trust were never logged, so backfilled rows leave them NULL.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_BACKFILL_DAYS = 30
_CYCLE_EVENT = "warming_cycle_completed"
_FLOOD_STATUSES = frozenset({"flood_wait", "peer_flood"})
# Stored column -> the key ``_build_cycle_result`` writes into the log row's extra.
_COUNTER_KEYS = (
    ("reads", "reads"),
    ("reactions", "reactions"),
    ("joins", "joined"),
    ("dms", "messages"),
    ("failures", "failures"),
)
_COLUMNS = ("cycles", *(column for column, _key in _COUNTER_KEYS), "floods")


def _create_table(connection: Connection) -> None:
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS warming_daily_stats ("
        " account_id VARCHAR NOT NULL,"
        " day VARCHAR NOT NULL,"
        " cycles INTEGER DEFAULT 0 NOT NULL,"
        " reads INTEGER DEFAULT 0 NOT NULL,"
        " reactions INTEGER DEFAULT 0 NOT NULL,"
        " joins INTEGER DEFAULT 0 NOT NULL,"
        " dms INTEGER DEFAULT 0 NOT NULL,"
        " failures INTEGER DEFAULT 0 NOT NULL,"
        " floods INTEGER DEFAULT 0 NOT NULL,"
        " phase VARCHAR,"
        " trust_score INTEGER,"
        " trust_band VARCHAR,"
        " updated_at VARCHAR NOT NULL,"
        " PRIMARY KEY (account_id, day))",
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_warming_daily_stats_day ON warming_daily_stats (day)",
    )


def _count(extra: dict[str, object], key: str) -> int:
    value = extra.get(key)
    return value if isinstance(value, int) and value > 0 else 0


def _aggregate(rows: list[tuple[str, str, str]]) -> dict[tuple[str, str], dict[str, int]]:
    totals: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: dict.fromkeys(_COLUMNS, 0))
    for account_id, created_at, raw_extra in rows:
        try:
            extra = json.loads(raw_extra) if raw_extra else {}
        except ValueError:
            extra = {}
        if not isinstance(extra, dict):
            extra = {}
        bucket = totals[account_id, created_at[:10]]
        bucket["cycles"] += 1
        for column, key in _COUNTER_KEYS:
            bucket[column] += _count(extra, key)
        bucket["floods"] += 1 if extra.get("status") in _FLOOD_STATUSES else 0
    return totals


def _backfill_warming_daily_stats(connection: Connection, now: datetime) -> None:
    has_logs = connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='logs'",
    ).first()
    if has_logs is None:
        return
    cutoff = (now - timedelta(days=_BACKFILL_DAYS)).isoformat()
    rows = connection.exec_driver_sql(
        "SELECT account_id, created_at, extra FROM logs"
        " WHERE event = ? AND account_id IS NOT NULL AND created_at >= ?",
        (_CYCLE_EVENT, cutoff),
    ).all()
    stamp = now.isoformat()
    placeholders = ", ".join("?" for _ in _COLUMNS)
    statement = (
        f"INSERT INTO warming_daily_stats (account_id, day, {', '.join(_COLUMNS)}, updated_at)"  # noqa: S608 - column names are module constants.
        f" VALUES (?, ?, {placeholders}, ?) ON CONFLICT (account_id, day) DO NOTHING"
    )
    for (account_id, day), counters in sorted(
        _aggregate([(str(r[0]), str(r[1]), str(r[2] or "")) for r in rows]).items(),
    ):
        connection.exec_driver_sql(
            statement,
            (account_id, day, *(counters[column] for column in _COLUMNS), stamp),
        )


def _add_warming_daily_stats(connection: Connection) -> None:
    _create_table(connection)
    _backfill_warming_daily_stats(connection, datetime.now(UTC))
