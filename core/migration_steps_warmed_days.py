"""Migration #77 — warming days become days of work.

Adds ``warmed_days`` / ``last_warmed_date`` to ``warming_account_state`` and seeds
existing stints from the activity log, so an account mid-stint neither starts over
at zero nor jumps ahead. Idempotent, per the contract in ``core.migrations``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_SECONDS_PER_DAY = 86_400

# One distinct UTC date per completed cycle that did real work: a clean cycle, or a
# failed one that still landed an action. The time zone of each account is not
# known here, so UTC dates are the estimate; the cap below keeps it honest.
_WORKED_DATES_SQL = (
    "SELECT DISTINCT substr(created_at, 1, 10) AS day FROM logs "
    "WHERE account_id = ? AND event = 'warming_cycle_completed' AND created_at >= ? "
    "AND (json_extract(extra, '$.status') = 'ok' OR ("
    "json_extract(extra, '$.status') = 'failed' AND ("
    "coalesce(json_extract(extra, '$.joined'), 0) + coalesce(json_extract(extra, '$.reads'), 0)"
    " + coalesce(json_extract(extra, '$.reactions'), 0)"
    " + coalesce(json_extract(extra, '$.messages'), 0)) > 0)) "
    "ORDER BY day"
)


def _add_warming_warmed_days(connection: Connection) -> None:
    if not _sqlite_table_exists(connection, "warming_account_state"):
        return
    columns = _sqlite_columns(connection, "warming_account_state")
    if "warmed_days" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE warming_account_state ADD COLUMN warmed_days INTEGER NOT NULL DEFAULT '0'",
        )
    if "last_warmed_date" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE warming_account_state ADD COLUMN last_warmed_date VARCHAR",
        )
    if _sqlite_table_exists(connection, "logs"):
        _backfill_warmed_days(connection, datetime.now(UTC))


def _backfill_warmed_days(connection: Connection, now: datetime) -> None:
    # Only rows never counted: a re-run, or a row the new runtime already counted,
    # is left alone.
    rows = connection.exec_driver_sql(
        "SELECT account_id, state, started_at, stopped_at FROM warming_account_state "
        "WHERE started_at IS NOT NULL AND warmed_days = 0 AND last_warmed_date IS NULL",
    ).all()
    for account_id, state, started_at, stopped_at in rows:
        elapsed = _elapsed_days(started_at, stopped_at if state == "idle" else None, now)
        if elapsed is None:
            continue
        days = [
            str(row[0])
            for row in connection.exec_driver_sql(_WORKED_DATES_SQL, (account_id, started_at)).all()
        ]
        if not days:
            continue
        connection.exec_driver_sql(
            "UPDATE warming_account_state SET warmed_days = ?, last_warmed_date = ? "
            "WHERE account_id = ?",
            (min(len(days), elapsed), days[-1], account_id),
        )


def _elapsed_days(started_at: str, stopped_at: str | None, now: datetime) -> int | None:
    """Whole days the stint has run — the ceiling on what the logs may claim."""
    try:
        started = _aware(datetime.fromisoformat(started_at))
        end = _aware(datetime.fromisoformat(stopped_at)) if stopped_at else now
    except ValueError:
        return None
    return max(0, int((end - started).total_seconds() // _SECONDS_PER_DAY))


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
