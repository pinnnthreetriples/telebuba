"""Migration 76 — flood memory: the spam-block history and the recovery timestamp.

``CREATE ... IF NOT EXISTS`` and a guarded ``ADD COLUMN``: ``create_all`` runs BEFORE
``apply_migrations``, so on a fresh database the schema comes from
``core._schema_tables`` and this is a no-op. The two spellings must agree — pinned by
``tests/core/test_migrations_flood_memory.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_FLOOD_EVENTS = (
    "CREATE TABLE IF NOT EXISTS warming_flood_events ("
    " id INTEGER NOT NULL,"
    " account_id VARCHAR NOT NULL,"
    " kind VARCHAR NOT NULL,"
    " action VARCHAR,"
    " created_at VARCHAR NOT NULL,"
    " PRIMARY KEY (id))"
)
_FLOOD_EVENTS_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_warming_flood_events_account"
    " ON warming_flood_events (account_id, created_at)"
)


def _add_warming_flood_memory(connection: Connection) -> None:
    # No FK to ``accounts``: the history must outlive a delete + re-import.
    connection.exec_driver_sql(_FLOOD_EVENTS)
    connection.exec_driver_sql(_FLOOD_EVENTS_INDEX)
    if not _sqlite_table_exists(connection, "warming_account_state"):
        return
    # NULL (every existing row) means "never released from quarantine": no ramp, no
    # DM block, so a deploy changes nothing for an account that is warming today.
    if "recovered_at" not in _sqlite_columns(connection, "warming_account_state"):
        connection.exec_driver_sql(
            "ALTER TABLE warming_account_state ADD COLUMN recovered_at VARCHAR",
        )
