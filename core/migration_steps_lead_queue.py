"""Migration 98 — the lead queue, and one live bulk run per kind.

The queue's tables are ``CREATE ... IF NOT EXISTS``: ``create_all`` runs BEFORE
``apply_migrations``, so on a fresh database they come from
``core._schema_lead_queue`` and this is a no-op for them. The two spellings must
agree — pinned by ``tests/core/test_migrations_lead_queue.py``.

``bulk_runs`` gains ``kind``, and its live-run index moves from "one live run" to
"one live run per kind", so the queue's run and an operator's run can both be live.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS lead_queue_items ("
    " id INTEGER NOT NULL,"
    " phone VARCHAR NOT NULL,"
    " phone_key VARCHAR NOT NULL,"
    " client_name VARCHAR,"
    " status VARCHAR NOT NULL,"
    " account_id VARCHAR,"
    " user_id INTEGER,"
    " recheck_account_id VARCHAR,"
    " run_id VARCHAR,"
    " error_code VARCHAR,"
    " created_at VARCHAR NOT NULL,"
    " looked_up_at VARCHAR,"
    " finished_at VARCHAR,"
    " updated_at VARCHAR NOT NULL,"
    " PRIMARY KEY (id),"
    " UNIQUE (phone_key))",
    "CREATE INDEX IF NOT EXISTS ix_lead_queue_items_status ON lead_queue_items (status, id)",
    "CREATE INDEX IF NOT EXISTS ix_lead_queue_items_account"
    " ON lead_queue_items (account_id, looked_up_at)",
    "CREATE TABLE IF NOT EXISTS lead_queue_settings ("
    " id INTEGER NOT NULL,"
    " account_ids_json VARCHAR NOT NULL,"
    " text VARCHAR NOT NULL,"
    " paused INTEGER DEFAULT 1 NOT NULL,"
    " owner_user_id VARCHAR,"
    " updated_at VARCHAR NOT NULL,"
    " PRIMARY KEY (id),"
    " CONSTRAINT ck_lead_queue_settings_single_row CHECK (id = 1))",
)


def _add_lead_queue(connection: Connection) -> None:
    for statement in _STATEMENTS:
        connection.exec_driver_sql(statement)
    if not _sqlite_table_exists(connection, "bulk_runs"):
        return
    if "kind" not in _sqlite_columns(connection, "bulk_runs"):
        connection.exec_driver_sql(
            "ALTER TABLE bulk_runs ADD COLUMN kind VARCHAR DEFAULT 'manual' NOT NULL"
        )
    connection.exec_driver_sql("DROP INDEX IF EXISTS ux_bulk_runs_live")
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX ux_bulk_runs_live ON bulk_runs (kind) WHERE status = 'running'"
    )
