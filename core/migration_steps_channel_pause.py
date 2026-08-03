"""Channel-pause migration body — a sibling of ``core.migration_steps``.

Its own module because ``core.migration_steps_neurocomment`` is at the file-size cap.
Idempotent, per the append-only migration contract in ``core.migrations``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_campaign_channel_pause(connection: Connection) -> None:
    # #42: persist "this channel will not let us write". K consecutive write failures end
    # a round: pause_rounds counts them, paused_until parks the channel for a flat window,
    # and the round after the last one unlinks the channel instead of pausing again. Both
    # lived in module dicts before, which a restart cleared — and the live app restarted 7
    # times in three days, so a four-day rule built on them never reached round 4. Existing
    # rows default to 0 / NULL: every channel starts the new rule with a clean slate. The
    # campaign-channel table is outside migration_steps._ALLOWED_TABLES, so the column
    # probe is inlined (a hard-coded table name, never user input).
    if not _sqlite_table_exists(connection, "neurocomment_campaign_channels"):
        return
    rows = (
        connection.exec_driver_sql("PRAGMA table_info(neurocomment_campaign_channels)")
        .mappings()
        .all()
    )
    columns = {str(row["name"]) for row in rows}
    if "pause_rounds" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE neurocomment_campaign_channels "
            "ADD COLUMN pause_rounds INTEGER NOT NULL DEFAULT 0",
        )
    if "paused_until" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE neurocomment_campaign_channels ADD COLUMN paused_until VARCHAR",
        )
