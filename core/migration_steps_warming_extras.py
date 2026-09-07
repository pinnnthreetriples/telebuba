"""Warming *extras* migration bodies — a sibling of ``core.migration_steps_discovery``.

Its own module for the reason those have: the older step modules sit at the
file-size cap. Idempotent, per the append-only migration contract in ``core.migrations``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_warming_settings_extra_toggles(connection: Connection) -> None:
    # One JSON column rather than 21 integer ones: the key set grows per PR and the
    # repository already merges it over ``EXTRA_TOGGLE_DEFAULTS`` on read, so a NULL
    # (every existing row) means "all defaults" and a deploy changes nothing live.
    if not _sqlite_table_exists(connection, "warming_settings"):
        return
    if "extra_toggles" not in _sqlite_columns(connection, "warming_settings"):
        connection.exec_driver_sql(
            "ALTER TABLE warming_settings ADD COLUMN extra_toggles VARCHAR",
        )
