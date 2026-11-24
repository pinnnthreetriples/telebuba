"""SQLAlchemy table of the removals Telebuba owes the host: tombstones, never inferences.

Mirrors ``core.migration_steps_pending_cleanups`` (123): ``create_all`` builds a fresh database
from THIS module while an existing one gets the migration, and
``tests/core/test_migrations_pending_cleanups.py`` compares the two.

A row says "this thing was asked to be removed and the removal has not been confirmed". It is
written BEFORE the removal is tried and deleted once it succeeded; what is to be removed is
only ever read off a row, never worked out from what the database lacks (a wrong or empty
database must not delete anything).
"""

from __future__ import annotations

from sqlalchemy import Column, String, Table

from core._schema_tables import _metadata

_pending_cleanups = Table(
    "pending_cleanups",
    _metadata,
    # What is to be removed (``pod``: an account's phone container) and its name.
    Column("kind", String, primary_key=True),
    Column("key", String, primary_key=True),
    Column("requested_at", String, nullable=False),
)
