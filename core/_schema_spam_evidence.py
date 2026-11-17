"""SQLAlchemy table of spam evidence (migration 116, ``core.migration_steps_spam_evidence``).

Its own module because ``core._schema_tables`` is near the file-size budget. ``create_all``
builds a fresh database from THIS module while an existing one gets the migration, and
``tests/core/test_migrations_spam_evidence.py`` compares the two. The projection columns on
``account_spam_status`` live with that table in ``core._schema_tables``.

No foreign key to ``accounts``, like the probe ledger: a late signal for a deleted account
costs nothing and the retention sweep removes it. Account-scoped rows are still deleted with
the account (``core.repositories._accounts_delete``).
"""

from __future__ import annotations

from sqlalchemy import Column, Index, Integer, String, Table, text

from core._schema_tables import _metadata

# One row per observation, or per burst of identical ones (``count``, ``last_seen_at``).
# Weights and half-lives are NOT stored: they are policy in ``services.spam_intel``.
_account_spam_evidence = Table(
    "account_spam_evidence",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("account_id", String, nullable=False),
    Column("kind", String, nullable=False),
    Column("direction", String, nullable=False),
    Column("observed_at", String, nullable=False),
    Column("expires_at", String, nullable=True),
    Column("source", String, nullable=False),
    Column("operation", String, nullable=True),
    Column("detail", String, nullable=True),
    Column("count", Integer, nullable=False, server_default=text("1")),
    Column("last_seen_at", String, nullable=False),
    Index("ix_account_spam_evidence_account", "account_id", "observed_at"),
    Index("ix_account_spam_evidence_observed", "observed_at"),
)
