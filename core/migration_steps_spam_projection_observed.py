"""Migration 119 — when the deciding observation of the spam projection was made.

One additive nullable column on ``account_spam_status`` (``primary_observed_at``): the account
card's source line says "PeerFlood, 2 hours ago", and the projection (migration 116) kept only
the kind, not the instant. ``create_all`` runs BEFORE ``apply_migrations``, so on a fresh
database the column comes from ``core._schema_tables`` and this is a no-op; the two spellings
must agree, which ``tests/core/test_migrations_spam_projection_observed.py`` pins. No backfill:
a projection folded before the column reads ``None`` and the next fold fills it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_spam_projection_observed(connection: Connection) -> None:
    if not _sqlite_table_exists(connection, "account_spam_status"):
        return
    existing = {
        str(row["name"])
        for row in connection.exec_driver_sql("PRAGMA table_info(account_spam_status)")
        .mappings()
        .all()
    }
    if "primary_observed_at" not in existing:
        connection.exec_driver_sql(
            "ALTER TABLE account_spam_status ADD COLUMN primary_observed_at VARCHAR"
        )
