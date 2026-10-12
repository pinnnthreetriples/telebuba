"""Migration 81 — the operator-bot notification settings table.

``CREATE ... IF NOT EXISTS``: ``create_all`` runs BEFORE ``apply_migrations``, so on a
fresh database the table comes from ``core._schema_notifications`` and this is a no-op.
The two spellings must agree — pinned by ``tests/core/test_migrations_notifications.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_notification_settings(connection: Connection) -> None:
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS notification_settings ("
        " id INTEGER NOT NULL,"
        " enabled INTEGER DEFAULT 0 NOT NULL,"
        " bot_token VARCHAR,"
        " chat_id VARCHAR,"
        " updated_at VARCHAR NOT NULL,"
        " PRIMARY KEY (id))",
    )
