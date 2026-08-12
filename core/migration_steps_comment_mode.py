"""Comment-mode migration body — a sibling of ``core.migration_steps``.

Its own module because ``core.migration_steps_neurocomment`` is at the file-size cap.
Idempotent, per the append-only migration contract in ``core.migrations``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_neurocomment_settings_comment_mode(connection: Connection) -> None:
    # Which message the fleet answers and how long the reply mode waits for a human.
    # Both land NOT NULL with the shipped defaults rather than nullable: this table is
    # the operator's stored *decision*, and a NULL mode would make every reader invent
    # its own answer for a choice nobody has made yet. Defaulting to ``first`` is what
    # makes the upgrade behaviour-neutral — an existing override keeps commenting
    # exactly as it did before the columns existed.
    # ``neurocomment_settings`` is outside ``migration_steps._ALLOWED_TABLES``, so the
    # column probe is inlined (a hard-coded table name, never user input) — same shape
    # as migrations #42 and #51 on the campaign-channel table.
    if not _sqlite_table_exists(connection, "neurocomment_settings"):
        return
    rows = connection.exec_driver_sql("PRAGMA table_info(neurocomment_settings)").mappings().all()
    existing = {str(row["name"]) for row in rows}
    if "comment_mode" not in existing:
        connection.exec_driver_sql(
            "ALTER TABLE neurocomment_settings "
            "ADD COLUMN comment_mode VARCHAR NOT NULL DEFAULT 'first'",
        )
    if "reply_wait_minutes" not in existing:
        connection.exec_driver_sql(
            "ALTER TABLE neurocomment_settings "
            "ADD COLUMN reply_wait_minutes INTEGER NOT NULL DEFAULT 10",
        )
