"""Migration 98 — outreach account protection.

The limits row gains its protection settings, and ``outreach_account_rests`` keeps the
last limit each account met. ``CREATE ... IF NOT EXISTS`` and a guarded ``ADD COLUMN``:
``create_all`` runs BEFORE ``apply_migrations``, so on a fresh database the schema
comes from ``core._schema_outreach`` and this is a no-op. The two spellings must
agree — pinned by ``tests/core/test_migrations_outreach.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_RESTS = (
    "CREATE TABLE IF NOT EXISTS outreach_account_rests ("
    " account_id VARCHAR NOT NULL,"
    " reason VARCHAR NOT NULL,"
    " limited_at VARCHAR NOT NULL,"
    " rest_until VARCHAR NOT NULL,"
    " PRIMARY KEY (account_id))"
)


def _add_outreach_protection(connection: Connection) -> None:
    connection.exec_driver_sql(_RESTS)
    if not _sqlite_table_exists(connection, "outreach_limits"):
        return
    # NULL (a row saved before this migration) means the protection defaults.
    if "protection_json" not in _sqlite_columns(connection, "outreach_limits"):
        connection.exec_driver_sql("ALTER TABLE outreach_limits ADD COLUMN protection_json VARCHAR")
