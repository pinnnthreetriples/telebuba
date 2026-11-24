"""Migration 125 — the ``account_security_snapshots`` table: the last read cloud-password settings.

``CREATE TABLE IF NOT EXISTS``: ``create_all`` runs BEFORE ``apply_migrations``, so on a fresh
database the table comes from ``core._schema_security_snapshots`` and this is a no-op. The two
spellings must agree, pinned by ``tests/core/test_migrations_security_snapshots.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_STATEMENT = (
    "CREATE TABLE IF NOT EXISTS account_security_snapshots ("
    " account_id VARCHAR NOT NULL,"
    " has_password BOOLEAN NOT NULL,"
    " has_recovery BOOLEAN NOT NULL,"
    " pending_reset_date VARCHAR,"
    " email_unconfirmed BOOLEAN NOT NULL,"
    " checked_at VARCHAR NOT NULL,"
    " PRIMARY KEY (account_id))"
)


def _add_account_security_snapshots(connection: Connection) -> None:
    connection.exec_driver_sql(_STATEMENT)
