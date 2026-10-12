"""Migration body for the fleet DM pause (``warming_settings.dm_paused_until``).

Its own module because the older step modules sit at the file-size cap. Idempotent,
per the append-only migration contract in ``core.migrations``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_warming_settings_dm_paused_until(connection: Connection) -> None:
    # NULL (the existing row): the fleet breaker never tripped, DMs are not paused.
    if not _sqlite_table_exists(connection, "warming_settings"):
        return
    if "dm_paused_until" not in _sqlite_columns(connection, "warming_settings"):
        connection.exec_driver_sql(
            "ALTER TABLE warming_settings ADD COLUMN dm_paused_until VARCHAR",
        )
