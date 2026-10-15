"""SQLAlchemy table for the warming channel searches, one row per search.

Its own module because ``core._schema_tables`` is near the file-size budget. Mirrors
migration 93 in ``core.migration_steps_channel_search``; the migration test compares
them. The rows are what spaces searches out: a warming account's own searches per day,
and the automatic refill per language. No foreign key: the history is pruned by age.
"""

from __future__ import annotations

from sqlalchemy import Column, Index, Integer, String, Table, text

from core._schema_tables import _metadata

_warming_channel_searches = Table(
    "warming_channel_searches",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # ``self`` (a warming account for itself), ``refill`` (automatic) or ``manual``.
    Column("origin", String, nullable=False),
    # NULL when no account could search (``no_account``).
    Column("account_id", String, nullable=True),
    Column("language", String, nullable=False),
    # ``ok``, ``flood``, ``failed`` or ``no_account``.
    Column("status", String, nullable=False),
    Column("found", Integer, nullable=False, server_default=text("0")),
    Column("added", Integer, nullable=False, server_default=text("0")),
    Column("created_at", String, nullable=False),
    Index("ix_warming_channel_searches_account", "account_id", "created_at"),
    Index("ix_warming_channel_searches_language", "language", "created_at"),
)
