"""Migration 96 — the journal may record a lifted spam block (``spam_block_lifted``).

A confirmed spam block is no longer for life: when @SpamBot later answers ``clean``,
warming clears ``spam_blocked_at`` and journals the lift, so the survival counter knows
the block stopped standing. SQLite cannot alter a CHECK, so the table is rebuilt under
the new constraint with every row, id and index carried over, exactly as migration 95
did for ``warming_stopped``.

Idempotent, per the contract in ``core.migrations``: the rebuild runs only while the
stored CHECK does not yet name ``spam_block_lifted``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_JOURNAL = "account_lifecycle_events"

# Frozen here on purpose, not read from ``schemas.lifecycle``: a migration body must
# keep producing the same schema whatever the live vocabulary grows into later.
_KINDS_96 = (
    "'warming_started','warmed','promoted','handed_off','quarantined',"
    "'spambot_verdict','session_dead','deleted','warming_stopped','spam_block_lifted'"
)
_NEW_JOURNAL = (
    "CREATE TABLE account_lifecycle_events_v96 ("
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
    f" CHECK (kind IN ({_KINDS_96})))"
)
_COPY_ROWS = (
    "INSERT INTO account_lifecycle_events_v96"
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


def _journal_sql(connection: Connection) -> str | None:
    row = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (_JOURNAL,),
    ).first()
    return str(row[0]) if row is not None else None


def _allow_lifecycle_spam_block_lifted(connection: Connection) -> None:
    sql = _journal_sql(connection)
    if sql is None or "'spam_block_lifted'" in sql:
        return
    connection.exec_driver_sql(_NEW_JOURNAL)
    connection.exec_driver_sql(_COPY_ROWS)
    connection.exec_driver_sql(f"DROP TABLE {_JOURNAL}")
    connection.exec_driver_sql(f"ALTER TABLE account_lifecycle_events_v96 RENAME TO {_JOURNAL}")
    for statement in _JOURNAL_INDEXES:
        connection.exec_driver_sql(statement)
