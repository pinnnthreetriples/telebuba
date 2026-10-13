"""SQLAlchemy table for each warming account's character (Прогрев 2.0, item 4.2).

Its own module because ``core._schema_tables`` is near the file-size budget. Mirrors
migration 86 in ``core.migration_steps_humanlike``; the migration test compares them.
``interests`` is a JSON list. No FK: a deleted account's row is purged by its service.
"""

from __future__ import annotations

from sqlalchemy import Column, Integer, String, Table

from core._schema_tables import _metadata

_warming_account_character = Table(
    "warming_account_character",
    _metadata,
    Column("account_id", String, primary_key=True),
    Column("language", String, nullable=False),
    Column("country", String, nullable=True),
    Column("age", Integer, nullable=False),
    Column("gender", String, nullable=False),
    Column("city", String, nullable=True),
    Column("occupation", String, nullable=False),
    Column("interests", String, nullable=False),
    Column("created_at", String, nullable=False),
)
