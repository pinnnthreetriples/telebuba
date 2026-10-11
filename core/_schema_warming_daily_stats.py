"""SQLAlchemy table for the per-account, per-day warming totals.

Its own module because ``core._schema_tables`` is near the file-size budget. Mirrors
``core.migration_steps_daily_stats``: ``create_all`` builds a fresh database from
THIS module while an existing one gets the migration, and
``tests/core/test_migrations_daily_stats.py`` compares the two.

No foreign key to ``accounts`` on purpose: the history outlives a deleted account.
"""

from __future__ import annotations

from sqlalchemy import Column, Index, Integer, String, Table, text

from core._schema_tables import _metadata

_warming_daily_stats = Table(
    "warming_daily_stats",
    _metadata,
    Column("account_id", String, primary_key=True),
    # ``YYYY-MM-DD``; which calendar it follows is the service's choice, not ours.
    Column("day", String, primary_key=True),
    Column("cycles", Integer, nullable=False, server_default=text("0")),
    Column("reads", Integer, nullable=False, server_default=text("0")),
    Column("reactions", Integer, nullable=False, server_default=text("0")),
    Column("joins", Integer, nullable=False, server_default=text("0")),
    Column("dms", Integer, nullable=False, server_default=text("0")),
    Column("failures", Integer, nullable=False, server_default=text("0")),
    Column("floods", Integer, nullable=False, server_default=text("0")),
    # Last value seen that day; NULL on rows backfilled from the activity log.
    Column("phase", String, nullable=True),
    Column("trust_score", Integer, nullable=True),
    Column("trust_band", String, nullable=True),
    Column("updated_at", String, nullable=False),
    # The retention sweep deletes by day across every account.
    Index("ix_warming_daily_stats_day", "day"),
)
