"""Migration 93 — channel searches go straight into the pool (Прогрев 2.0 follow-up).

Its own module because the older step modules sit at the file-size cap. Three things,
each idempotent per the append-only contract in ``core.migrations``:

- ``warming_channels.found_by``: the warming account whose own search found the row;
- ``warming_channel_searches``: one row per search, which is what spaces them out;
- every proposal still ``pending`` becomes ``approved``. They passed the same filters a
  search now approves on its own, so they join the pool the operator can prune.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_warming_channel_search(connection: Connection) -> None:
    if _sqlite_table_exists(connection, "warming_channels"):
        if "found_by" not in _sqlite_columns(connection, "warming_channels"):
            connection.exec_driver_sql("ALTER TABLE warming_channels ADD COLUMN found_by VARCHAR")
        connection.exec_driver_sql(
            "UPDATE warming_channels SET status = 'approved' WHERE status = 'pending'"
        )
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS warming_channel_searches ("
        "  id INTEGER NOT NULL,"
        "  origin VARCHAR NOT NULL,"
        "  account_id VARCHAR,"
        "  language VARCHAR NOT NULL,"
        "  status VARCHAR NOT NULL,"
        "  found INTEGER DEFAULT 0 NOT NULL,"
        "  added INTEGER DEFAULT 0 NOT NULL,"
        "  created_at VARCHAR NOT NULL,"
        "  PRIMARY KEY (id)"
        ")",
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_warming_channel_searches_account"
        " ON warming_channel_searches (account_id, created_at)",
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_warming_channel_searches_language"
        " ON warming_channel_searches (language, created_at)",
    )
