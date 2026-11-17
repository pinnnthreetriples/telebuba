"""Migration 116 — spam evidence: the evidence table and the projection columns.

``CREATE TABLE IF NOT EXISTS`` and guarded ``ADD COLUMN``: ``create_all`` runs BEFORE
``apply_migrations``, so on a fresh database everything comes from ``core._schema_spam_evidence``
and ``core._schema_tables`` and this is a no-op. The two spellings must agree — pinned by
``tests/core/test_migrations_spam_evidence.py``.

The columns on ``account_spam_status`` are all nullable and additive: ``status``, ``detail`` and
``checked_at`` are untouched, so every reader that existed before keeps working. No backfill:
``services.spam_intel.verdicts_for`` derives a verdict from the legacy reading while an account
has no projection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS account_spam_evidence ("
    " id INTEGER NOT NULL,"
    " account_id VARCHAR NOT NULL,"
    " kind VARCHAR NOT NULL,"
    " direction VARCHAR NOT NULL,"
    " observed_at VARCHAR NOT NULL,"
    " expires_at VARCHAR,"
    " source VARCHAR NOT NULL,"
    " operation VARCHAR,"
    " detail VARCHAR,"
    " count INTEGER DEFAULT 1 NOT NULL,"
    " last_seen_at VARCHAR NOT NULL,"
    " PRIMARY KEY (id))",
    "CREATE INDEX IF NOT EXISTS ix_account_spam_evidence_account"
    " ON account_spam_evidence (account_id, observed_at)",
    "CREATE INDEX IF NOT EXISTS ix_account_spam_evidence_observed"
    " ON account_spam_evidence (observed_at)",
)

# (column, SQL type) — fixed names, never user input.
_PROJECTION_COLUMNS = (
    ("confidence", "VARCHAR"),
    ("valid_until", "VARCHAR"),
    ("primary_kind", "VARCHAR"),
    ("last_spambot_at", "VARCHAR"),
    ("needs_probe", "INTEGER"),
    ("fold_status", "VARCHAR"),
    ("fold_reason", "VARCHAR"),
    ("tos_restricted", "INTEGER"),
    ("tos_reason", "VARCHAR"),
    ("folded_at", "VARCHAR"),
)


def _add_spam_evidence(connection: Connection) -> None:
    for statement in _STATEMENTS:
        connection.exec_driver_sql(statement)
    if not _sqlite_table_exists(connection, "account_spam_status"):
        return
    existing = {
        str(row["name"])
        for row in connection.exec_driver_sql("PRAGMA table_info(account_spam_status)")
        .mappings()
        .all()
    }
    for name, sql_type in _PROJECTION_COLUMNS:
        if name not in existing:
            connection.exec_driver_sql(
                f"ALTER TABLE account_spam_status ADD COLUMN {name} {sql_type}"
            )
