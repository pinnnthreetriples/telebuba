"""Migration 122 — the ``account_key_intents`` table: flows that make a new auth key.

``CREATE TABLE IF NOT EXISTS``: ``create_all`` runs BEFORE ``apply_migrations``, so on a
fresh database the table comes from ``core._schema_session_audits`` and this is a no-op.
The two spellings must agree — pinned by ``tests/core/test_key_intents.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS account_key_intents ("
    " id INTEGER NOT NULL,"
    " account_id VARCHAR NOT NULL,"
    " kind VARCHAR NOT NULL,"
    " device_type VARCHAR NOT NULL,"
    " session_name VARCHAR NOT NULL,"
    " stage VARCHAR NOT NULL,"
    " api_id INTEGER,"
    " new_hash VARCHAR,"
    " old_hash VARCHAR,"
    " old_key_mark VARCHAR,"
    " device_json VARCHAR,"
    " started_at VARCHAR NOT NULL,"
    " closed_at VARCHAR,"
    " outcome VARCHAR,"
    " PRIMARY KEY (id))",
    "CREATE INDEX IF NOT EXISTS ix_account_key_intents_account ON account_key_intents (account_id)",
)


def _add_account_key_intents(connection: Connection) -> None:
    for statement in _STATEMENTS:
        connection.exec_driver_sql(statement)
