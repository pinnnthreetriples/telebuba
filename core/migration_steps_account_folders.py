"""Migration 71 — account folders: the operator's named lists of accounts.

``CREATE ... IF NOT EXISTS`` throughout: ``create_all`` runs BEFORE ``apply_migrations``,
so on a fresh database the schema comes from ``core.repositories.account_folders._tables``
and this is a no-op. The two spellings must agree down to keys and indexes — pinned by
``tests/core/test_migrations_account_folders.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_account_folders(connection: Connection) -> None:
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS account_folders ("
        " folder_id VARCHAR NOT NULL,"
        " name VARCHAR NOT NULL,"
        " name_key VARCHAR NOT NULL,"
        " created_at VARCHAR NOT NULL,"
        " updated_at VARCHAR NOT NULL,"
        " PRIMARY KEY (folder_id))",
    )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_account_folders_name_key"
        " ON account_folders (name_key)",
    )
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS account_folder_members ("
        " folder_id VARCHAR NOT NULL,"
        " account_id VARCHAR NOT NULL,"
        " added_at VARCHAR NOT NULL,"
        " PRIMARY KEY (folder_id, account_id),"
        " FOREIGN KEY(folder_id) REFERENCES account_folders (folder_id) ON DELETE CASCADE,"
        " FOREIGN KEY(account_id) REFERENCES accounts (account_id))",
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_account_folder_members_account"
        " ON account_folder_members (account_id)",
    )
