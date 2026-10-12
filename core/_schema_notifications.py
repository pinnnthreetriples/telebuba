"""SQLAlchemy table for the operator-bot notification settings (a singleton row).

Its own module because ``core._schema_tables`` is near the file-size budget. Mirrors
``core.migration_steps_notifications``: ``create_all`` builds a fresh database from
THIS module while an existing one gets migration 81, and
``tests/core/test_migrations_notifications.py`` compares the two.
"""

from __future__ import annotations

from sqlalchemy import Column, Integer, String, Table, text

from core._schema_tables import _metadata

_notification_settings = Table(
    "notification_settings",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("enabled", Integer, nullable=False, server_default=text("0")),
    # The bot token is a secret: masked by the service before any response.
    Column("bot_token", String, nullable=True),
    Column("chat_id", String, nullable=True),
    Column("updated_at", String, nullable=False),
)
