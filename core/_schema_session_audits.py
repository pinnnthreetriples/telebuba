"""SQLAlchemy tables for the session audit: the latest snapshot, the history, the re-key.

Its own module because ``core._schema_tables`` is near the file-size budget. Mirrors
``core.migration_steps_session_audits`` (104) and ``core.migration_steps_rekeys`` (107):
``create_all`` builds a fresh database from THIS module while an existing one gets the
migrations, and ``tests/core/test_migrations_session_audits.py`` /
``tests/core/test_migrations_rekeys.py`` compare the two.

The snapshot holds the sessions of an account as the last audit saw them, including the
IP and device of sessions that are not ours; it goes with the account. The history keeps
only counts, so it can say "the last audit saw N sessions at T" long after.
"""

from __future__ import annotations

from sqlalchemy import Column, Index, Integer, String, Table, text

from core._schema_tables import _metadata

_account_session_audits = Table(
    "account_session_audits",
    _metadata,
    Column("account_id", String, primary_key=True),
    Column("checked_at", String, nullable=False),
    Column("total", Integer, nullable=False),
    Column("foreign_count", Integer, nullable=False),
    Column("ttl_days", Integer, nullable=True),
    # ``list[AccountSession]`` as JSON.
    Column("sessions", String, nullable=False, server_default=text("'[]'")),
)

_account_session_audit_events = Table(
    "account_session_audit_events",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("account_id", String, nullable=False),
    Column("checked_at", String, nullable=False),
    Column("total", Integer, nullable=False),
    Column("foreign_count", Integer, nullable=False),
    Column("killed_count", Integer, nullable=False, server_default=text("0")),
    Index("ix_account_session_audit_events_account", "account_id", "id"),
)

# One row per account: its last re-key, and the old authorization still to be ended. The
# hash is an authorization identifier, not a credential.
_account_rekeys = Table(
    "account_rekeys",
    _metadata,
    Column("account_id", String, primary_key=True),
    Column("old_hash", String, nullable=False),
    Column("rekeyed_at", String, nullable=False),
    Column("close_after", String, nullable=True),
    Column("closed_at", String, nullable=True),
    Index("ix_account_rekeys_due", "close_after"),
)
