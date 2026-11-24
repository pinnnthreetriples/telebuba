"""Migration 121 — the refused-candidate mark (``accounts.twofa_pending_refused_at``).

Set when Telegram definitively refused the pending cloud-password candidate, cleared
whenever that candidate changes or goes away. Nullable and additive; no backfill (a row
that holds a refused candidate today is marked the next time the candidate is asked
about). Idempotent, per the append-only contract in ``core.migrations``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_account_twofa_refused(connection: Connection) -> None:
    if not _sqlite_table_exists(connection, "accounts"):
        return
    if "twofa_pending_refused_at" not in _sqlite_columns(connection, "accounts"):
        connection.exec_driver_sql(
            "ALTER TABLE accounts ADD COLUMN twofa_pending_refused_at VARCHAR",
        )
