"""SQLAlchemy table for the account lifecycle journal (migration #74).

Deliberately NO foreign key to ``accounts``: the journal must outlive the account
it describes, so a delete leaves these rows alone and ``tg_user_id`` /
``stint_started_at`` are snapshots taken when the row is written. The logs
retention sweep never touches this table either.

Every constraint and index here MIRRORS ``core.migration_steps_lifecycle``:
``create_all`` builds a fresh database from this module and the migration no-ops
on its ``IF NOT EXISTS``; ``tests/core/test_migrations_lifecycle.py`` compares both.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, Column, Index, Integer, String, Table, text

from core._schema_tables import _metadata
from schemas.lifecycle import LIFECYCLE_EVENT_KINDS

_KIND_CHECK = f"kind IN ({','.join(repr(kind) for kind in LIFECYCLE_EVENT_KINDS)})"

_account_lifecycle_events = Table(
    "account_lifecycle_events",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("account_id", String, nullable=False),
    Column("tg_user_id", BigInteger, nullable=True),
    # Start of the warming stint the event belongs to (``warming_account_state.started_at``
    # at write time); NULL when the account was never warmed.
    Column("stint_started_at", String, nullable=True),
    Column("kind", String, nullable=False),
    Column("created_at", String, nullable=False),
    # Small JSON object: the @SpamBot status, the delete reason, the flood status.
    Column("extra", String, nullable=False, server_default=text("'{}'")),
    # 1 = reconstructed from the ``logs`` table by migration #74, not written live.
    Column("backfilled", Integer, nullable=False, server_default=text("0")),
    # The ``logs.id`` a backfilled row came from; the unique index makes a re-run
    # of the backfill insert nothing twice. NULL for every live row.
    Column("source_log_id", Integer, nullable=True),
    CheckConstraint(_KIND_CHECK),
    Index("ix_account_lifecycle_events_account", "account_id", "id"),
    Index("ix_account_lifecycle_events_kind", "kind", "created_at"),
    Index("ux_account_lifecycle_events_source_log", "source_log_id", unique=True),
)
