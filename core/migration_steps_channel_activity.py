"""Channel-activity migration body — a sibling of ``core.migration_steps``.

Its own module because ``core.migration_steps_neurocomment`` is at the file-size cap.
Idempotent, per the append-only migration contract in ``core.migrations``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_campaign_channel_last_post(connection: Connection) -> None:
    # When the listener last saw this channel publish, so the inactive-channel rule can
    # tell a dead channel from a quiet week. Existing rows stay NULL rather than being
    # backfilled with "now": a backfill would claim we saw a post we never saw, and give
    # every channel a fresh week before the rule could look at it. NULL ages from
    # ``created_at``, which is a fact we do have. The campaign-channel table is outside
    # migration_steps._ALLOWED_TABLES, so the column probe is inlined (a hard-coded table
    # name, never user input) — same as migration #42 on this table.
    if not _sqlite_table_exists(connection, "neurocomment_campaign_channels"):
        return
    rows = (
        connection.exec_driver_sql("PRAGMA table_info(neurocomment_campaign_channels)")
        .mappings()
        .all()
    )
    if "last_post_at" not in {str(row["name"]) for row in rows}:
        connection.exec_driver_sql(
            "ALTER TABLE neurocomment_campaign_channels ADD COLUMN last_post_at VARCHAR",
        )
