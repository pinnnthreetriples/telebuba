"""Warming-settings LLM-key migration bodies — a sibling of ``core.migration_steps``.

Its own module because the older step modules sit at the file-size cap. Idempotent,
per the append-only migration contract in ``core.migrations``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

# Added by #5 for a settings-page control that never shipped: quiet hours come from
# ``settings.warming.active_hours_*`` and the daily cap from the phase budget, so no
# reader has ever touched them. Hard-coded names, never user input.
_DEAD_WARMING_SETTINGS_COLUMNS = (
    "quiet_hours_enabled",
    "quiet_hours_start",
    "quiet_hours_end",
    "max_daily_actions",
)


def _drop_warming_settings_dead_columns(connection: Connection) -> None:
    # A fresh DB still runs #5 (append-only), which re-adds them — this step then drops
    # them again, so both routes end on the same schema as ``create_all``.
    if not _sqlite_table_exists(connection, "warming_settings"):
        return
    existing = _sqlite_columns(connection, "warming_settings")
    for column_name in _DEAD_WARMING_SETTINGS_COLUMNS:
        if column_name in existing:
            connection.exec_driver_sql(
                f"ALTER TABLE warming_settings DROP COLUMN {column_name}",
            )


def _add_warming_settings_deepseek_key(connection: Connection) -> None:
    # Nullable like the other key columns: NULL/"" falls back to ``DEEPSEEK__API_KEY``
    # on read, so an env-only deployment keeps generating text unchanged.
    if not _sqlite_table_exists(connection, "warming_settings"):
        return
    if "deepseek_api_key" not in _sqlite_columns(connection, "warming_settings"):
        connection.exec_driver_sql(
            "ALTER TABLE warming_settings ADD COLUMN deepseek_api_key VARCHAR",
        )


def _add_warming_settings_text_llm_provider(connection: Connection) -> None:
    # Nullable: NULL reads as the default provider, which is how every text was routed
    # before the choice existed.
    if not _sqlite_table_exists(connection, "warming_settings"):
        return
    if "text_llm_provider" not in _sqlite_columns(connection, "warming_settings"):
        connection.exec_driver_sql(
            "ALTER TABLE warming_settings ADD COLUMN text_llm_provider VARCHAR",
        )
