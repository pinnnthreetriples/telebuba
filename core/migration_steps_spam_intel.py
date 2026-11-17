"""Migration 114 — the spam-intel ledger: attempts, per-account state, reply samples.

``CREATE TABLE IF NOT EXISTS``: ``create_all`` runs BEFORE ``apply_migrations``, so on a
fresh database the tables come from ``core._schema_spam_intel`` and this is a no-op. The two
spellings must agree — pinned by ``tests/core/test_migrations_spam_intel.py``.

The attempts table is seeded once from the last 14 days of ``spambot_verdict`` journal rows,
so the first day after the deploy does not start with an empty ledger and a free burst. The
seed runs only while the table is empty, which makes a second run a no-op.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS spam_probe_attempts ("
    " id INTEGER NOT NULL,"
    " account_id VARCHAR NOT NULL,"
    " proxy_key VARCHAR,"
    " kind VARCHAR DEFAULT 'probe' NOT NULL,"
    " purpose VARCHAR NOT NULL,"
    " caller VARCHAR NOT NULL,"
    " started_at VARCHAR NOT NULL,"
    " finished_at VARCHAR,"
    " outcome VARCHAR DEFAULT 'started' NOT NULL,"
    " error_type VARCHAR,"
    " duration_ms INTEGER,"
    " PRIMARY KEY (id))",
    "CREATE INDEX IF NOT EXISTS ix_spam_probe_attempts_account"
    " ON spam_probe_attempts (account_id, started_at)",
    "CREATE INDEX IF NOT EXISTS ix_spam_probe_attempts_proxy"
    " ON spam_probe_attempts (proxy_key, started_at)",
    "CREATE INDEX IF NOT EXISTS ix_spam_probe_attempts_started ON spam_probe_attempts (started_at)",
    "CREATE TABLE IF NOT EXISTS spam_probe_state ("
    " account_id VARCHAR NOT NULL,"
    " last_attempt_at VARCHAR,"
    " last_success_at VARCHAR,"
    " fail_streak INTEGER DEFAULT 0 NOT NULL,"
    " next_allowed_at VARCHAR,"
    " last_caller VARCHAR,"
    " last_outcome VARCHAR,"
    " last_error VARCHAR,"
    " history_read_at VARCHAR,"
    " updated_at VARCHAR,"
    " PRIMARY KEY (account_id))",
    "CREATE TABLE IF NOT EXISTS spam_reply_samples ("
    " text_hash VARCHAR NOT NULL,"
    " sample VARCHAR NOT NULL,"
    " lang VARCHAR,"
    " source VARCHAR DEFAULT 'probe' NOT NULL,"
    " legacy_status VARCHAR,"
    " strict_status VARCHAR,"
    " has_date INTEGER DEFAULT 0 NOT NULL,"
    # The two flags below came with migration 118 (``migration_steps_spam_sample_flags``); a
    # database that reaches 114 now gets them here, one that was already past it gets them there.
    " multi_message INTEGER DEFAULT 0 NOT NULL,"
    " has_markup INTEGER DEFAULT 0 NOT NULL,"
    " first_seen_at VARCHAR NOT NULL,"
    " last_seen_at VARCHAR NOT NULL,"
    " seen_count INTEGER DEFAULT 1 NOT NULL,"
    " PRIMARY KEY (text_hash))",
)

# Journal ``created_at`` is ISO with a ``T``; ``strftime`` spells the cut-off the same way.
_SEED = (
    "INSERT INTO spam_probe_attempts"
    " (account_id, proxy_key, kind, purpose, caller, started_at, finished_at, outcome)"
    " SELECT e.account_id,"
    " (SELECT a.proxy_id FROM accounts a WHERE a.account_id = e.account_id),"
    " 'probe', 'routine', 'backfill', e.created_at, e.created_at,"
    " COALESCE(json_extract(e.extra, '$.status'), 'unknown')"
    " FROM account_lifecycle_events e"
    " WHERE e.kind = 'spambot_verdict'"
    " AND e.created_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-14 days')"
    " AND NOT EXISTS (SELECT 1 FROM spam_probe_attempts)"
)


def _add_spam_probe_ledger(connection: Connection) -> None:
    for statement in _STATEMENTS:
        connection.exec_driver_sql(statement)
    if _sqlite_table_exists(connection, "account_lifecycle_events") and _sqlite_table_exists(
        connection, "accounts"
    ):
        connection.exec_driver_sql(_SEED)
