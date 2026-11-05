"""Migration 108 — the ``account_own_authorizations`` table: sessions Telebuba itself made.

``CREATE TABLE IF NOT EXISTS``: ``create_all`` runs BEFORE ``apply_migrations``, so on a
fresh database the table comes from ``core._schema_own_authorizations`` and this is a no-op.
The two spellings must agree — pinned by ``tests/core/test_migrations_own_authorizations.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_STATEMENT = (
    "CREATE TABLE IF NOT EXISTS account_own_authorizations ("
    " account_id VARCHAR NOT NULL,"
    " hash VARCHAR NOT NULL,"
    " kind VARCHAR NOT NULL,"
    " recorded_at VARCHAR NOT NULL,"
    " PRIMARY KEY (account_id, hash))"
)


def _add_account_own_authorizations(connection: Connection) -> None:
    connection.exec_driver_sql(_STATEMENT)
