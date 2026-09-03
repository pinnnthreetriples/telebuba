"""Channel-discovery migration bodies — a sibling of ``core.migration_steps``.

Its own module because both existing step modules are near the file-size cap.
Holds the Telemetr key column on the settings row (#37) and the per-campaign
discovery candidate table (#38). Both are idempotent, per the append-only
migration contract in ``core.migrations``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_warming_settings_telemetr_key(connection: Connection) -> None:
    # The settings row is the app's single home for external provider keys (Gemini,
    # OpenAI already live here), so the Telemetr.io discovery key joins them rather
    # than growing a second secret surface on the neurocomment settings row.
    # Guard the table so a hand-built legacy DB without it is a no-op, not an error
    # (PRAGMA table_info on a missing table returns no rows, not an error).
    if not _sqlite_table_exists(connection, "warming_settings"):
        return
    if "telemetr_api_key" not in _sqlite_columns(connection, "warming_settings"):
        connection.exec_driver_sql(
            "ALTER TABLE warming_settings ADD COLUMN telemetr_api_key VARCHAR",
        )


def _add_neurocomment_discovery_candidates(connection: Connection) -> None:
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS neurocomment_discovery_candidates ("
        "  campaign_id VARCHAR NOT NULL REFERENCES neurocomment_campaigns(campaign_id),"
        "  channel VARCHAR NOT NULL,"
        "  title VARCHAR NOT NULL DEFAULT '',"
        "  subscribers INTEGER,"
        "  source VARCHAR NOT NULL,"
        "  qualified_at VARCHAR,"
        "  qualify_error VARCHAR,"
        "  created_at VARCHAR NOT NULL,"
        "  PRIMARY KEY (campaign_id, channel)"
        ")",
    )
    # Serves both "next unqualified candidate" and the grouped progress count.
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_nc_discovery_campaign_qualified "
        "ON neurocomment_discovery_candidates(campaign_id, qualified_at)",
    )


def _add_discovery_kind_and_seen(connection: Connection) -> None:
    # #60: candidates learn whether they are a channel or a group, and the fleet-wide
    # "already shown" table backs the request's ``hide_seen``.
    # Raw PRAGMA, like #45 on the join log: ``_sqlite_columns`` whitelists its table names.
    # Skipped on a hand-built legacy DB without the table — a no-op, not an error.
    if _sqlite_table_exists(connection, "neurocomment_discovery_candidates"):
        rows = connection.exec_driver_sql(
            "PRAGMA table_info(neurocomment_discovery_candidates)",
        ).mappings()
        if "kind" not in {str(row["name"]) for row in rows}:
            connection.exec_driver_sql(
                "ALTER TABLE neurocomment_discovery_candidates "
                "ADD COLUMN kind VARCHAR NOT NULL DEFAULT 'channel'",
            )
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS neurocomment_discovery_seen ("
        "  channel VARCHAR PRIMARY KEY,"
        "  first_seen_at VARCHAR NOT NULL,"
        "  last_seen_at VARCHAR NOT NULL"
        ")",
    )
