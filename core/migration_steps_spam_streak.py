"""Migration body for the warming @SpamBot unreadable streak.

Its own module because the older step modules sit at the file-size cap. Idempotent,
per the append-only migration contract in ``core.migrations``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_warming_state_spam_unknown_streak(connection: Connection) -> None:
    # Default 0 (every existing row): no unreadable check on record yet.
    if not _sqlite_table_exists(connection, "warming_account_state"):
        return
    if "spam_unknown_streak" not in _sqlite_columns(connection, "warming_account_state"):
        connection.exec_driver_sql(
            "ALTER TABLE warming_account_state "
            "ADD COLUMN spam_unknown_streak INTEGER NOT NULL DEFAULT 0",
        )
