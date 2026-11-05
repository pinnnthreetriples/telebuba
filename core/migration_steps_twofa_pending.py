"""Migration 109 — the pending cloud password (``accounts.twofa_password_pending``).

A candidate password is staged here before ``account.updatePasswordSettings`` and
promoted once Telegram confirmed, so a crash or a lost reply never leaves only the
old password. Idempotent, per the append-only contract in ``core.migrations``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_COLUMNS = (
    ("twofa_password_pending", "VARCHAR"),
    ("twofa_pending_at", "VARCHAR"),
)


def _add_account_twofa_pending(connection: Connection) -> None:
    if not _sqlite_table_exists(connection, "accounts"):
        return
    existing = _sqlite_columns(connection, "accounts")
    for name, sql_type in _COLUMNS:
        if name not in existing:
            connection.exec_driver_sql(f"ALTER TABLE accounts ADD COLUMN {name} {sql_type}")
