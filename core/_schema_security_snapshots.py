"""SQLAlchemy table of the last read of an account's cloud-password settings.

Its own module, mirroring ``core._schema_own_authorizations``;
``core.migration_steps_security_snapshots`` (125) is the spelling an existing database gets, and
``tests/core/test_migrations_security_snapshots.py`` compares the two. Booleans and dates only:
never the e-mail pattern, never the hint.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, String, Table

from core._schema_tables import _metadata

_account_security_snapshots = Table(
    "account_security_snapshots",
    _metadata,
    Column("account_id", String, primary_key=True),
    Column("has_password", Boolean, nullable=False),
    Column("has_recovery", Boolean, nullable=False),
    Column("pending_reset_date", String, nullable=True),
    Column("email_unconfirmed", Boolean, nullable=False),
    Column("checked_at", String, nullable=False),
)
