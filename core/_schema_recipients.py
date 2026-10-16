"""SQLAlchemy tables for recipient history and "not found" re-checks (migration #98).

``recipient_history`` has one row per first message the bulk-message tool sent (or
may have sent): the recipient's key, the account and the time — no text, no name.
``contact_rechecks`` queues numbers a lookup answered "not on Telegram" for another
account the next UTC day. No foreign key to ``accounts``: a deleted account must not
erase whom it wrote to, or "one client — one account" would hand its clients on.

Mirrors ``core.migration_steps_recipients``; ``tests/core/test_migrations_recipients.py``
compares the two spellings.
"""

from __future__ import annotations

from sqlalchemy import Column, Index, Integer, String, Table

from core._schema_tables import _metadata

_recipient_history = Table(
    "recipient_history",
    _metadata,
    Column("id", Integer, primary_key=True),
    # A Telegram user id in canonical digits, or a username casefolded without "@".
    Column("peer", String, nullable=False),
    # The phone the recipient was found by, digits only; NULL when typed by hand.
    Column("phone", String, nullable=True),
    Column("account_id", String, nullable=False),
    Column("sent_at", String, nullable=False),
    Index("ix_recipient_history_peer", "peer"),
    Index("ix_recipient_history_phone", "phone"),
)

_contact_rechecks = Table(
    "contact_rechecks",
    _metadata,
    # Digits only, so "+7999…" and "7999…" are one queued number.
    Column("phone_key", String, primary_key=True),
    # The number as the lookup normalised it, sent as-is when re-checked.
    Column("phone", String, nullable=False),
    # The account that answered "not found"; the re-check goes to any other one.
    Column("checked_by", String, nullable=False),
    # Midnight UTC after the first check.
    Column("due_at", String, nullable=False),
    Column("created_at", String, nullable=False),
)
