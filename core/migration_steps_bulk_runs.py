"""Migration 91 — persistent bulk-message runs and the default run schedule.

``CREATE ... IF NOT EXISTS`` and a guarded ``ADD COLUMN``: ``create_all`` runs BEFORE
``apply_migrations``, so on a fresh database the schema comes from
``core._schema_bulk_runs`` / ``core._schema_outreach`` and this is a no-op. The two
spellings must agree — pinned by ``tests/core/test_migrations_bulk_runs.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS bulk_runs ("
    " id VARCHAR NOT NULL,"
    " owner_user_id VARCHAR NOT NULL,"
    " status VARCHAR NOT NULL,"
    " request_json VARCHAR NOT NULL,"
    " cancel_requested INTEGER DEFAULT 0 NOT NULL,"
    " next_send_at VARCHAR,"
    " wait_reason VARCHAR,"
    " created_at VARCHAR NOT NULL,"
    " updated_at VARCHAR NOT NULL,"
    " finished_at VARCHAR,"
    " PRIMARY KEY (id))",
    "CREATE INDEX IF NOT EXISTS ix_bulk_runs_status ON bulk_runs (status)",
    "CREATE TABLE IF NOT EXISTS bulk_run_items ("
    " id INTEGER NOT NULL,"
    " run_id VARCHAR NOT NULL,"
    " ordinal INTEGER NOT NULL,"
    " account_id VARCHAR NOT NULL,"
    " recipient VARCHAR NOT NULL,"
    " peer VARCHAR NOT NULL,"
    " tz_name VARCHAR NOT NULL,"
    " time_basis VARCHAR NOT NULL,"
    " status VARCHAR NOT NULL,"
    " error_code VARCHAR,"
    " retry_after_seconds INTEGER,"
    " handed_over INTEGER DEFAULT 0 NOT NULL,"
    " finished_seq INTEGER,"
    " finished_at VARCHAR,"
    " PRIMARY KEY (id))",
    "CREATE INDEX IF NOT EXISTS ix_bulk_run_items_run ON bulk_run_items (run_id, status)",
    "CREATE TABLE IF NOT EXISTS bulk_run_accounts ("
    " run_id VARCHAR NOT NULL,"
    " account_id VARCHAR NOT NULL,"
    " stop_code VARCHAR,"
    " retry_after_seconds INTEGER,"
    " ready_at VARCHAR,"
    " series_count INTEGER DEFAULT 0 NOT NULL,"
    " PRIMARY KEY (run_id, account_id))",
)


def _add_bulk_runs(connection: Connection) -> None:
    for statement in _STATEMENTS:
        connection.exec_driver_sql(statement)
    if not _sqlite_table_exists(connection, "outreach_limits"):
        return
    # NULL (the row saved before this migration) means the schedule's defaults.
    if "schedule_json" not in _sqlite_columns(connection, "outreach_limits"):
        connection.exec_driver_sql("ALTER TABLE outreach_limits ADD COLUMN schedule_json VARCHAR")
