"""Migration 100 — the per-account passkey table.

``CREATE TABLE IF NOT EXISTS``: ``create_all`` runs BEFORE ``apply_migrations``, so on
a fresh database the table comes from ``core._schema_passkeys`` and this is a no-op.
The two spellings must agree — pinned by ``tests/core/test_migrations_passkeys.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_PASSKEYS = (
    "CREATE TABLE IF NOT EXISTS account_passkeys ("
    " account_id VARCHAR NOT NULL,"
    " credential_id VARCHAR NOT NULL,"
    " user_handle VARCHAR NOT NULL,"
    " vault_key_name VARCHAR NOT NULL,"
    " created_at VARCHAR NOT NULL,"
    " PRIMARY KEY (account_id))"
)


def _add_account_passkeys(connection: Connection) -> None:
    connection.exec_driver_sql(_PASSKEYS)
