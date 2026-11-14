"""Migrations 107 and 113 — the account re-key table, and its terminal ``problem`` column.

107: which old authorization is still to be ended. 113 adds ``problem``: why the old one cannot
be ended by Telebuba and the operator has to (see ``services.accounts.session_rekey``).

``CREATE TABLE IF NOT EXISTS``: ``create_all`` runs BEFORE ``apply_migrations``, so on a
fresh database the table comes from ``core._schema_session_audits`` and this is a no-op.
The two spellings must agree — pinned by ``tests/core/test_migrations_rekeys.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS account_rekeys ("
    " account_id VARCHAR NOT NULL,"
    " old_hash VARCHAR NOT NULL,"
    " rekeyed_at VARCHAR NOT NULL,"
    " close_after VARCHAR,"
    " closed_at VARCHAR,"
    " PRIMARY KEY (account_id))",
    "CREATE INDEX IF NOT EXISTS ix_account_rekeys_due ON account_rekeys (close_after)",
)


def _add_account_rekeys(connection: Connection) -> None:
    for statement in _STATEMENTS:
        connection.exec_driver_sql(statement)


def _add_account_rekey_problem(connection: Connection) -> None:
    if not _sqlite_table_exists(connection, "account_rekeys"):
        return
    if "problem" not in _sqlite_columns(connection, "account_rekeys"):
        connection.exec_driver_sql("ALTER TABLE account_rekeys ADD COLUMN problem VARCHAR")
