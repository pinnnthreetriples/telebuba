"""Migration body for the warming pace card (``warming_settings.tuning_*``, item 5.2).

Its own module because the older step modules sit at the file-size cap. Idempotent,
per the append-only migration contract in ``core.migrations``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_warming_settings_tuning(connection: Connection) -> None:
    # NULL (the existing row): «Обычно» — the engine exactly as ``.env`` configures it.
    if not _sqlite_table_exists(connection, "warming_settings"):
        return
    columns = _sqlite_columns(connection, "warming_settings")
    if "tuning_preset" not in columns:
        connection.exec_driver_sql("ALTER TABLE warming_settings ADD COLUMN tuning_preset VARCHAR")
    if "tuning_custom" not in columns:
        connection.exec_driver_sql("ALTER TABLE warming_settings ADD COLUMN tuning_custom VARCHAR")
