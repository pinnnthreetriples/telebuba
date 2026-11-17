"""SQLAlchemy table of spam appeals (migration 117, ``core.migration_steps_spam_appeals``).

Its own module because ``core._schema_tables`` is near the file-size budget. ``create_all``
builds a fresh database from THIS module while an existing one gets the migration, and
``tests/core/test_migrations_spam_appeals.py`` compares the two.

No foreign key to ``accounts``, like the probe ledger and the evidence: the rows are deleted
with the account (``core.repositories._accounts_delete``). At most ONE open row per account, held
by a partial unique index, so two clicks cannot file twice.
"""

from __future__ import annotations

from sqlalchemy import Column, Index, Integer, String, Table, text

from core._schema_tables import _metadata

_spam_appeals = Table(
    "spam_appeals",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("account_id", String, nullable=False),
    Column("filed_at", String, nullable=False),
    Column("filed_by", String, nullable=False),
    # The verdict and the day the limit ends (ISO date, none = open-ended) when it was filed.
    Column("verdict", String, nullable=False),
    Column("until_day", String, nullable=True),
    Column("closed_at", String, nullable=True),
    Column("closed_by_kind", String, nullable=True),
    Index("ix_spam_appeals_account", "account_id", "filed_at"),
    Index(
        "ux_spam_appeals_open",
        "account_id",
        unique=True,
        sqlite_where=text("closed_at IS NULL"),
    ),
)
