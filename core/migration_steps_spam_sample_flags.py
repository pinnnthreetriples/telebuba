"""Migration 118 — structural flags on the redacted @SpamBot reply samples.

Two additive columns on ``spam_reply_samples`` (``multi_message``, ``has_markup``), both
``INTEGER NOT NULL DEFAULT 0``: what the human-like probe saw around a reply (the bot sent more
than one message; a message carried buttons). ``create_all`` runs BEFORE ``apply_migrations``, so
on a fresh database the columns come from ``core._schema_spam_intel`` and this is a no-op; the two
spellings must agree, which ``tests/core/test_migrations_spam_sample_flags.py`` pins. Old rows read
``0`` (unknown is recorded as "not seen"); nothing is backfilled.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

# Fixed names, never user input.
_FLAG_COLUMNS = ("multi_message", "has_markup")


def _add_spam_sample_flags(connection: Connection) -> None:
    if not _sqlite_table_exists(connection, "spam_reply_samples"):
        return
    existing = {
        str(row["name"])
        for row in connection.exec_driver_sql("PRAGMA table_info(spam_reply_samples)")
        .mappings()
        .all()
    }
    for name in _FLAG_COLUMNS:
        if name not in existing:
            connection.exec_driver_sql(
                f"ALTER TABLE spam_reply_samples ADD COLUMN {name} INTEGER DEFAULT 0 NOT NULL"
            )
