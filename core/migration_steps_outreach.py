"""Migrations 89 and 90 — outreach pacing tables.

89: the limits singleton and the per-day usage counters. 90: saved slider presets.

``CREATE ... IF NOT EXISTS``: ``create_all`` runs BEFORE ``apply_migrations``, so on a
fresh database the schema comes from ``core._schema_outreach`` and this is a no-op.
The two spellings must agree — pinned by ``tests/core/test_migrations_outreach.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_LIMITS = (
    "CREATE TABLE IF NOT EXISTS outreach_limits ("
    " id INTEGER NOT NULL,"
    " contact_imports_per_day INTEGER NOT NULL,"
    " lookup_batch_size INTEGER NOT NULL,"
    " lookup_min_delay_seconds FLOAT NOT NULL,"
    " lookup_max_delay_seconds FLOAT NOT NULL,"
    " silent_limit_streak INTEGER NOT NULL,"
    " first_messages_per_day INTEGER,"
    " bulk_min_delay_seconds FLOAT NOT NULL,"
    " bulk_max_delay_seconds FLOAT NOT NULL,"
    " updated_at VARCHAR NOT NULL,"
    " PRIMARY KEY (id),"
    " CONSTRAINT ck_outreach_limits_single_row CHECK (id = 1))"
)
_USAGE = (
    "CREATE TABLE IF NOT EXISTS outreach_daily_usage ("
    " account_id VARCHAR NOT NULL,"
    " day VARCHAR NOT NULL,"
    " contact_imports INTEGER DEFAULT 0 NOT NULL,"
    " contact_matches INTEGER DEFAULT 0 NOT NULL,"
    " first_messages INTEGER DEFAULT 0 NOT NULL,"
    " updated_at VARCHAR NOT NULL,"
    " PRIMARY KEY (account_id, day))"
)


def _add_outreach_tables(connection: Connection) -> None:
    connection.exec_driver_sql(_LIMITS)
    connection.exec_driver_sql(_USAGE)


_PRESETS = (
    "CREATE TABLE IF NOT EXISTS outreach_presets ("
    " id VARCHAR NOT NULL,"
    " name VARCHAR NOT NULL,"
    " name_key VARCHAR NOT NULL,"
    " pause_seconds INTEGER NOT NULL,"
    " first_messages_per_day INTEGER NOT NULL,"
    " contact_imports_per_day INTEGER NOT NULL,"
    " lookup_batch_size INTEGER NOT NULL,"
    " updated_at VARCHAR NOT NULL,"
    " PRIMARY KEY (id),"
    " UNIQUE (name_key))"
)


def _add_outreach_presets(connection: Connection) -> None:
    connection.exec_driver_sql(_PRESETS)
