"""Migration 98 — recipient history, "not found" re-checks and the default rules.

``CREATE ... IF NOT EXISTS`` and a guarded ``ADD COLUMN``: ``create_all`` runs BEFORE
``apply_migrations``, so on a fresh database the schema comes from
``core._schema_recipients`` / ``core._schema_outreach`` and this is a no-op. The two
spellings must agree — pinned by ``tests/core/test_migrations_recipients.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS recipient_history ("
    " id INTEGER NOT NULL,"
    " peer VARCHAR NOT NULL,"
    " phone VARCHAR,"
    " account_id VARCHAR NOT NULL,"
    " sent_at VARCHAR NOT NULL,"
    " PRIMARY KEY (id))",
    "CREATE INDEX IF NOT EXISTS ix_recipient_history_peer ON recipient_history (peer)",
    "CREATE INDEX IF NOT EXISTS ix_recipient_history_phone ON recipient_history (phone)",
    "CREATE TABLE IF NOT EXISTS contact_rechecks ("
    " phone_key VARCHAR NOT NULL,"
    " phone VARCHAR NOT NULL,"
    " checked_by VARCHAR NOT NULL,"
    " due_at VARCHAR NOT NULL,"
    " created_at VARCHAR NOT NULL,"
    " PRIMARY KEY (phone_key))",
)


def _add_recipient_history(connection: Connection) -> None:
    for statement in _STATEMENTS:
        connection.exec_driver_sql(statement)
    if not _sqlite_table_exists(connection, "outreach_limits"):
        return
    # NULL (a row saved before this migration) means the rules' defaults.
    if "recipients_json" not in _sqlite_columns(connection, "outreach_limits"):
        connection.exec_driver_sql("ALTER TABLE outreach_limits ADD COLUMN recipients_json VARCHAR")
