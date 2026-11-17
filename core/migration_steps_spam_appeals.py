"""Migration 117 — spam appeals: the record that the operator filed an appeal by hand.

``CREATE TABLE IF NOT EXISTS``: ``create_all`` runs BEFORE ``apply_migrations``, so on a fresh
database the table comes from ``core._schema_spam_appeals`` and this is a no-op. The two
spellings must agree — pinned by ``tests/core/test_migrations_spam_appeals.py``. Purely
additive; no backfill.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS spam_appeals ("
    " id INTEGER NOT NULL,"
    " account_id VARCHAR NOT NULL,"
    " filed_at VARCHAR NOT NULL,"
    " filed_by VARCHAR NOT NULL,"
    " verdict VARCHAR NOT NULL,"
    " until_day VARCHAR,"
    " closed_at VARCHAR,"
    " closed_by_kind VARCHAR,"
    " PRIMARY KEY (id))",
    "CREATE INDEX IF NOT EXISTS ix_spam_appeals_account ON spam_appeals (account_id, filed_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_spam_appeals_open"
    " ON spam_appeals (account_id) WHERE closed_at IS NULL",
)


def _add_spam_appeals(connection: Connection) -> None:
    for statement in _STATEMENTS:
        connection.exec_driver_sql(statement)
