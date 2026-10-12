"""Warming channel label migration body — a sibling of ``core.migration_steps_warming_extras``.

Its own module for the reason those have: the older step modules sit at the
file-size cap. Idempotent, per the append-only migration contract in ``core.migrations``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

# Every existing row is an operator's own pick: ``manual`` and ``approved``. The labels
# stay NULL (unknown) rather than guessed, so each channel remains a fallback for every
# account until a read or the operator names its language.
_COLUMNS: tuple[tuple[str, str], ...] = (
    ("language", "VARCHAR"),
    ("country", "VARCHAR"),
    ("kind", "VARCHAR"),
    ("subscribers", "INTEGER"),
    ("last_post_at", "VARCHAR"),
    ("source", "VARCHAR NOT NULL DEFAULT 'manual'"),
    ("status", "VARCHAR NOT NULL DEFAULT 'approved'"),
)


def _add_warming_channel_labels(connection: Connection) -> None:
    if not _sqlite_table_exists(connection, "warming_channels"):
        return
    present = _sqlite_columns(connection, "warming_channels")
    for name, ddl in _COLUMNS:
        if name not in present:
            connection.exec_driver_sql(f"ALTER TABLE warming_channels ADD COLUMN {name} {ddl}")
