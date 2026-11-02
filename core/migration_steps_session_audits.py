"""Migration 104 — the session audit tables: the latest snapshot and the audit history.

``CREATE TABLE IF NOT EXISTS``: ``create_all`` runs BEFORE ``apply_migrations``, so on
a fresh database the tables come from ``core._schema_session_audits`` and this is a
no-op. The two spellings must agree — pinned by
``tests/core/test_migrations_session_audits.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS account_session_audits ("
    " account_id VARCHAR NOT NULL,"
    " checked_at VARCHAR NOT NULL,"
    " total INTEGER NOT NULL,"
    " foreign_count INTEGER NOT NULL,"
    " ttl_days INTEGER,"
    " sessions VARCHAR DEFAULT '[]' NOT NULL,"
    " PRIMARY KEY (account_id))",
    "CREATE TABLE IF NOT EXISTS account_session_audit_events ("
    " id INTEGER NOT NULL,"
    " account_id VARCHAR NOT NULL,"
    " checked_at VARCHAR NOT NULL,"
    " total INTEGER NOT NULL,"
    " foreign_count INTEGER NOT NULL,"
    " killed_count INTEGER DEFAULT 0 NOT NULL,"
    " PRIMARY KEY (id))",
    "CREATE INDEX IF NOT EXISTS ix_account_session_audit_events_account"
    " ON account_session_audit_events (account_id, id)",
)


def _add_account_session_audits(connection: Connection) -> None:
    for statement in _STATEMENTS:
        connection.exec_driver_sql(statement)
