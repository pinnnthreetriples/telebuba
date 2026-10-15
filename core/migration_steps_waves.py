"""Migrations 94-95 — warming waves and the confirmed spam block (Прогрев 2.0 follow-up).

94 adds two columns to ``warming_account_state``:

* ``wave`` — the account's warming wave: 1 for its first stint, +1 for every fresh stint
  of the same account life. Existing rows are backfilled from the lifecycle journal:
  the number of distinct stints its ``warming_started`` rows opened in the account's
  current life (after its last ``deleted`` row and not before the live account row was
  created — the same two boundaries ``services.lifecycle_journal`` draws), at least 1.
* ``spam_blocked_at`` — when @SpamBot confirmed a spam block on the quarantine re-check.
  A row parked by the old three-strikes ladder (``error`` with ``quarantine_exhausted``)
  is exactly that confirmation, so it moves into the new ``spam_blocked`` state, stamped
  with the moment it was parked.

95 lets the journal hold ``warming_stopped`` (a wave ended without a graduation: the
operator's stop, or the spam-block stop). SQLite cannot alter a CHECK, so the table is
rebuilt under the new constraint with every row, id and index carried over.

Idempotent, per the contract in ``core.migrations``: the columns are added only when
missing, the backfill only touches rows still at the default, and the rebuild runs only
while the stored CHECK does not yet name ``warming_stopped``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_STATE = "warming_account_state"
_JOURNAL = "account_lifecycle_events"

_WAVE_BACKFILL = """
UPDATE warming_account_state
   SET wave = MAX(1, (
       SELECT COUNT(DISTINCT COALESCE(e.stint_started_at, e.created_at))
         FROM account_lifecycle_events e
         JOIN accounts a ON a.account_id = e.account_id
        WHERE e.account_id = warming_account_state.account_id
          AND e.kind = 'warming_started'
          AND e.created_at >= a.created_at
          AND e.id > COALESCE(
              (SELECT MAX(d.id) FROM account_lifecycle_events d
                WHERE d.account_id = e.account_id AND d.kind = 'deleted'),
              0)))
 WHERE wave = 1
"""

_EXHAUSTED_TO_SPAM_BLOCKED = """
UPDATE warming_account_state
   SET state = 'spam_blocked', spam_blocked_at = COALESCE(spam_blocked_at, updated_at)
 WHERE state = 'error' AND last_event = 'quarantine_exhausted'
"""

# Frozen here on purpose, not read from ``schemas.lifecycle``: a migration body must
# keep producing the same schema whatever the live vocabulary grows into later.
_KINDS_95 = (
    "'warming_started','warmed','promoted','handed_off','quarantined',"
    "'spambot_verdict','session_dead','deleted','warming_stopped'"
)
_NEW_JOURNAL = (
    "CREATE TABLE account_lifecycle_events_v95 ("
    " id INTEGER NOT NULL,"
    " account_id VARCHAR NOT NULL,"
    " tg_user_id BIGINT,"
    " stint_started_at VARCHAR,"
    " kind VARCHAR NOT NULL,"
    " created_at VARCHAR NOT NULL,"
    " extra VARCHAR DEFAULT '{}' NOT NULL,"
    " backfilled INTEGER DEFAULT 0 NOT NULL,"
    " source_log_id INTEGER,"
    " PRIMARY KEY (id),"
    f" CHECK (kind IN ({_KINDS_95})))"
)
_COPY_ROWS = (
    "INSERT INTO account_lifecycle_events_v95"
    " (id, account_id, tg_user_id, stint_started_at, kind, created_at, extra,"
    " backfilled, source_log_id)"
    " SELECT id, account_id, tg_user_id, stint_started_at, kind, created_at, extra,"
    " backfilled, source_log_id FROM account_lifecycle_events"
)
_JOURNAL_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_account_lifecycle_events_account"
    " ON account_lifecycle_events (account_id, id)",
    "CREATE INDEX IF NOT EXISTS ix_account_lifecycle_events_kind"
    " ON account_lifecycle_events (kind, created_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_account_lifecycle_events_source_log"
    " ON account_lifecycle_events (source_log_id)",
)


def _add_warming_state_wave(connection: Connection) -> None:
    if not _sqlite_table_exists(connection, _STATE):
        return
    present = _sqlite_columns(connection, _STATE)
    if "wave" not in present:
        connection.exec_driver_sql(
            f"ALTER TABLE {_STATE} ADD COLUMN wave INTEGER NOT NULL DEFAULT '1'",
        )
    if "spam_blocked_at" not in present:
        connection.exec_driver_sql(f"ALTER TABLE {_STATE} ADD COLUMN spam_blocked_at VARCHAR")
    if _sqlite_table_exists(connection, _JOURNAL) and _sqlite_table_exists(connection, "accounts"):
        connection.exec_driver_sql(_WAVE_BACKFILL)
    # A hand-built legacy table may predate the columns the old ladder wrote.
    if {"last_event", "updated_at"} <= present:
        connection.exec_driver_sql(_EXHAUSTED_TO_SPAM_BLOCKED)


def _journal_sql(connection: Connection) -> str | None:
    row = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (_JOURNAL,),
    ).first()
    return str(row[0]) if row is not None else None


def _allow_lifecycle_warming_stopped(connection: Connection) -> None:
    sql = _journal_sql(connection)
    if sql is None or "'warming_stopped'" in sql:
        return
    connection.exec_driver_sql(_NEW_JOURNAL)
    connection.exec_driver_sql(_COPY_ROWS)
    connection.exec_driver_sql("DROP TABLE account_lifecycle_events")
    connection.exec_driver_sql(
        "ALTER TABLE account_lifecycle_events_v95 RENAME TO account_lifecycle_events"
    )
    for statement in _JOURNAL_INDEXES:
        connection.exec_driver_sql(statement)
