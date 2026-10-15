"""SQLAlchemy tables for the lead queue (migration #97).

``lead_queue_items`` holds one row per queued phone number for its whole life, so a
number is never queued (and messaged) twice; ``lead_queue_settings`` is the queue's
singleton (accounts, message, paused). No foreign key to ``accounts``: a deleted
account must not erase whom it found or wrote to.

Mirrors ``core.migration_steps_lead_queue``; ``tests/core/test_migrations_lead_queue.py``
compares the two spellings.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, Index, Integer, String, Table, text

from core._schema_tables import _metadata

_lead_queue_items = Table(
    "lead_queue_items",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("phone", String, nullable=False),
    Column("phone_key", String, nullable=False, unique=True),
    Column("client_name", String, nullable=True),
    Column("status", String, nullable=False),
    Column("account_id", String, nullable=True),
    Column("user_id", Integer, nullable=True),
    Column("recheck_account_id", String, nullable=True),
    # The bulk run that sends (or sent) this lead's first message.
    Column("run_id", String, nullable=True),
    Column("error_code", String, nullable=True),
    Column("created_at", String, nullable=False),
    Column("looked_up_at", String, nullable=True),
    Column("finished_at", String, nullable=True),
    Column("updated_at", String, nullable=False),
    Index("ix_lead_queue_items_status", "status", "id"),
    Index("ix_lead_queue_items_account", "account_id", "looked_up_at"),
)

_lead_queue_settings = Table(
    "lead_queue_settings",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("account_ids_json", String, nullable=False),
    Column("text", String, nullable=False),
    Column("paused", Integer, nullable=False, server_default=text("1")),
    Column("owner_user_id", String, nullable=True),
    Column("updated_at", String, nullable=False),
    CheckConstraint("id = 1", name="ck_lead_queue_settings_single_row"),
)
