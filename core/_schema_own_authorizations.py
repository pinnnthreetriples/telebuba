"""SQLAlchemy table of the authorizations Telebuba itself created for an account.

Its own module, mirroring ``core._schema_session_audits``. Mirrors
``core.migration_steps_own_authorizations`` (108): ``create_all`` builds a fresh database from
THIS module while an existing one gets the migration, and
``tests/core/test_migrations_own_authorizations.py`` compares the two.

An authorization Telebuba made (its own Telegram Web login) is not one of the account's
Telethon devices, so the audit cannot learn it by asking a client; it is recorded here when the
login ends and then counts as ours. The hash is an authorization identifier, not a credential.
"""

from __future__ import annotations

from sqlalchemy import Column, String, Table

from core._schema_tables import _metadata

_account_own_authorizations = Table(
    "account_own_authorizations",
    _metadata,
    Column("account_id", String, primary_key=True),
    Column("hash", String, primary_key=True),
    # What made it: ``web`` (Telebuba's Telegram Web login).
    Column("kind", String, nullable=False),
    Column("recorded_at", String, nullable=False),
)
