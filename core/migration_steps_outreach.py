"""Migration 89 — outreach pacing: the limits singleton and the per-day usage counters.

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
