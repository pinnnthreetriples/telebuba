"""SQLAlchemy tables for account folders.

Every ``ForeignKey`` and index below MIRRORS ``core.migration_steps_account_folders``:
``create_all`` runs before ``apply_migrations``, so on a fresh database THIS builds the
schema and the migration no-ops on its ``IF NOT EXISTS``.
``tests/core/test_migrations_account_folders.py`` compares both.

``name_key`` is the casefolded name: SQLite's ``NOCASE`` folds ASCII only, so a unique
index over it would let «Основные» and «основные» coexist. A membership cascades from
its folder; the account key has no ``ON DELETE CASCADE`` (the house rule), so the
account delete purges memberships itself (``core.repositories._accounts_delete``).
"""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Index, String, Table

from core.db import _metadata

_account_folders = Table(
    "account_folders",
    _metadata,
    Column("folder_id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("name_key", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Index("ux_account_folders_name_key", "name_key", unique=True),
)

_account_folder_members = Table(
    "account_folder_members",
    _metadata,
    Column(
        "folder_id",
        String,
        ForeignKey("account_folders.folder_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("account_id", String, ForeignKey("accounts.account_id"), primary_key=True),
    Column("added_at", String, nullable=False),
    Index("ix_account_folder_members_account", "account_id"),
)
