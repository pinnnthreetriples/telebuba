"""Migration 123 — the ``pending_cleanups`` table: removals asked for and not yet confirmed.

``CREATE TABLE IF NOT EXISTS``: ``create_all`` runs BEFORE ``apply_migrations``, so on a fresh
database the table comes from ``core._schema_pending_cleanups`` and this is a no-op. The two
spellings must agree — pinned by ``tests/core/test_migrations_pending_cleanups.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_STATEMENT = (
    "CREATE TABLE IF NOT EXISTS pending_cleanups ("
    " kind VARCHAR NOT NULL,"
    " key VARCHAR NOT NULL,"
    " requested_at VARCHAR NOT NULL,"
    " PRIMARY KEY (kind, key))"
)


def _add_pending_cleanups(connection: Connection) -> None:
    connection.exec_driver_sql(_STATEMENT)
