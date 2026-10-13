"""SQLAlchemy tables for outreach pacing (migration #89).

``outreach_limits`` is the operator's settings singleton; ``outreach_daily_usage``
counts per account and UTC day what the contact lookup and the bulk-message tool
spent. No foreign key to ``accounts``: the counters must survive a delete and
re-import of the same account, or a fresh import would reset its daily allowance.

Mirrors ``core.migration_steps_outreach``; ``tests/core/test_migrations_outreach.py``
compares the two spellings.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, Float, Integer, String, Table, text

from core._schema_tables import _metadata

_outreach_limits = Table(
    "outreach_limits",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("contact_imports_per_day", Integer, nullable=False),
    Column("lookup_batch_size", Integer, nullable=False),
    Column("lookup_min_delay_seconds", Float, nullable=False),
    Column("lookup_max_delay_seconds", Float, nullable=False),
    Column("silent_limit_streak", Integer, nullable=False),
    # NULL: no daily cap on bulk first messages.
    Column("first_messages_per_day", Integer, nullable=True),
    Column("bulk_min_delay_seconds", Float, nullable=False),
    Column("bulk_max_delay_seconds", Float, nullable=False),
    Column("updated_at", String, nullable=False),
    CheckConstraint("id = 1", name="ck_outreach_limits_single_row"),
)

_outreach_daily_usage = Table(
    "outreach_daily_usage",
    _metadata,
    Column("account_id", String, primary_key=True),
    # UTC ``YYYY-MM-DD``.
    Column("day", String, primary_key=True),
    Column("contact_imports", Integer, nullable=False, server_default=text("0")),
    Column("contact_matches", Integer, nullable=False, server_default=text("0")),
    Column("first_messages", Integer, nullable=False, server_default=text("0")),
    Column("updated_at", String, nullable=False),
)
