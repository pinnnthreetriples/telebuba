"""Migration 126 — the cloud-password rejection budget (``accounts.twofa_rejected_*``).

``twofa_rejected_count`` is how many wrong answers Telegram gave this account in the current
window and ``twofa_rejected_since`` when the window started. Nullable and additive; no
backfill (an account starts with a full budget). Idempotent, per the append-only contract in
``core.migrations``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_COLUMNS = (
    ("twofa_rejected_count", "INTEGER"),
    ("twofa_rejected_since", "VARCHAR"),
)


def _add_account_twofa_attempts(connection: Connection) -> None:
    if not _sqlite_table_exists(connection, "accounts"):
        return
    existing = _sqlite_columns(connection, "accounts")
    for name, sql_type in _COLUMNS:
        if name not in existing:
            connection.exec_driver_sql(f"ALTER TABLE accounts ADD COLUMN {name} {sql_type}")  # nosec B608 - constant names
