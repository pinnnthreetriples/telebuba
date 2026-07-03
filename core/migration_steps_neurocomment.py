"""Neurocomment migration bodies — split from ``core.migration_steps`` for size.

Kept in its own module so ``core.migration_steps`` stays under the file-size
budget. Holds the neurocomment schema bodies (tables #11, runtime #12, comment
indexes #13, challenges #14, readiness human-skip #15, settings #19). The
generic SQLite helpers are imported from ``core.migration_steps`` and these
bodies are re-imported back into it so ``core.migrations`` keeps importing every
step from ``core.migration_steps`` unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.migration_steps import _sqlite_columns, _sqlite_table_exists

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _add_neurocomment_tables(connection: Connection) -> None:
    # Ф1 data layer (#114). Mirrors the SQLAlchemy tables in core.db; created
    # idempotently here so existing databases gain them on the next engine init.
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS neurocomment_campaigns ("
        "  campaign_id VARCHAR PRIMARY KEY,"
        "  name VARCHAR NOT NULL,"
        "  prompt VARCHAR NOT NULL,"
        "  status VARCHAR NOT NULL,"
        "  created_at VARCHAR NOT NULL,"
        "  updated_at VARCHAR NOT NULL"
        ")",
    )
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS neurocomment_campaign_channels ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  campaign_id VARCHAR NOT NULL REFERENCES neurocomment_campaigns(campaign_id),"
        "  channel VARCHAR NOT NULL,"
        "  active INTEGER NOT NULL,"
        "  created_at VARCHAR NOT NULL"
        ")",
    )
    # The invariant, enforced in the DB: a channel sits in at most one ACTIVE
    # campaign. Partial unique index (SQLite >= 3.35) — inactive links are
    # exempt, so a channel can move between campaigns over its lifetime.
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_neurocomment_channel_one_active_campaign "
        "ON neurocomment_campaign_channels(channel) WHERE active = 1",
    )
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS neurocomment_campaign_accounts ("
        "  campaign_id VARCHAR NOT NULL REFERENCES neurocomment_campaigns(campaign_id),"
        "  account_id VARCHAR NOT NULL REFERENCES accounts(account_id),"
        "  created_at VARCHAR NOT NULL,"
        "  PRIMARY KEY (campaign_id, account_id)"
        ")",
    )
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS neurocomment_linked_groups ("
        "  channel VARCHAR PRIMARY KEY,"
        "  linked_chat_id BIGINT,"
        "  comments_enabled INTEGER NOT NULL,"
        "  checked_at VARCHAR NOT NULL"
        ")",
    )
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS neurocomment_readiness ("
        "  account_id VARCHAR NOT NULL REFERENCES accounts(account_id),"
        "  channel VARCHAR NOT NULL,"
        "  joined INTEGER NOT NULL,"
        "  captcha_passed INTEGER NOT NULL,"
        "  ready INTEGER NOT NULL,"
        "  checked_at VARCHAR NOT NULL,"
        "  PRIMARY KEY (account_id, channel)"
        ")",
    )
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS neurocomment_comments ("
        "  channel VARCHAR NOT NULL,"
        "  post_id INTEGER NOT NULL,"
        "  campaign_id VARCHAR NOT NULL REFERENCES neurocomment_campaigns(campaign_id),"
        "  account_id VARCHAR NOT NULL REFERENCES accounts(account_id),"
        "  status VARCHAR NOT NULL,"
        "  comment_text VARCHAR,"
        "  comment_msg_id INTEGER,"
        "  created_at VARCHAR NOT NULL,"
        "  updated_at VARCHAR NOT NULL,"
        "  PRIMARY KEY (channel, post_id)"
        ")",
    )


def _add_neurocomment_runtime(connection: Connection) -> None:
    # #119: single-row table persisting the active listener account id so the
    # engine can re-point the listener at boot. id is pinned to 1; NULL
    # listener_account_id means the listener is stopped.
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS neurocomment_runtime ("
        "  id INTEGER PRIMARY KEY CHECK (id = 1),"
        "  listener_account_id VARCHAR,"
        "  updated_at VARCHAR NOT NULL"
        ")",
    )


def _add_neurocomment_settings(connection: Connection) -> None:
    # #19: single-row operator-editable neurocomment limits. Empty until the
    # operator saves; reads fall back to settings.neurocomment config defaults.
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS neurocomment_settings ("
        "  id INTEGER PRIMARY KEY CHECK (id = 1),"
        "  max_comments_per_hour INTEGER NOT NULL,"
        "  max_comments_per_channel_per_day INTEGER NOT NULL,"
        "  reply_delay_min_seconds REAL NOT NULL,"
        "  reply_delay_max_seconds REAL NOT NULL,"
        "  min_trust_score INTEGER NOT NULL,"
        "  updated_at VARCHAR NOT NULL"
        ")",
    )


def _add_neurocomment_comment_indexes(connection: Connection) -> None:
    # Secondary indexes for the quota gate + bulk account selection. The PK
    # (channel, post_id) serves the per-post claim/mark lookups but not the
    # account-wide hourly count, the per-channel day count, or the campaign+channel
    # recent-posted dedup read — each would full-scan neurocomment_comments as it
    # grows. Column order matches those query shapes (verified via EXPLAIN QUERY PLAN).
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_nc_comments_account_status_created "
        "ON neurocomment_comments(account_id, status, created_at)",
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_nc_comments_channel_account_status_created "
        "ON neurocomment_comments(channel, account_id, status, created_at)",
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_nc_comments_campaign_channel_status_created "
        "ON neurocomment_comments(campaign_id, channel, status, created_at)",
    )


def _add_neurocomment_challenges(connection: Connection) -> None:
    # Ф2 #120: one audit-and-cache table (the cache is a ``WHERE outcome='solved'``
    # projection — no dual-write) plus a per-campaign solver override column.
    # No data remap for the captcha_gated -> chat_restricted state split: the
    # channel status is *derived* from neurocomment_readiness booleans, never
    # stored, so the same (joined, captcha_passed) row now reads as chat_restricted
    # once board._channel_status changes.
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS neurocomment_challenges ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  challenge_hash VARCHAR NOT NULL,"
        "  account_id VARCHAR NOT NULL,"
        "  channel VARCHAR NOT NULL,"
        "  raw_text VARCHAR NOT NULL,"
        "  button_labels_json VARCHAR NOT NULL,"
        "  decision_json VARCHAR,"
        "  outcome VARCHAR NOT NULL DEFAULT 'pending',"
        "  decided_at VARCHAR NOT NULL,"
        "  outcome_at VARCHAR"
        ")",
    )
    # Cache fast-path: lookup a solved decision by hash.
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_nc_challenges_hash_outcome "
        "ON neurocomment_challenges(challenge_hash, outcome)",
    )
    # Engine outcome resolution: latest pending row for an (account, channel).
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_nc_challenges_account_channel_decided "
        "ON neurocomment_challenges(account_id, channel, decided_at DESC)",
    )
    if "solver_enabled" not in _sqlite_columns(connection, "neurocomment_campaigns"):
        # NULL = defer to the global challenge_solver_enabled flag (per-campaign override).
        connection.exec_driver_sql(
            "ALTER TABLE neurocomment_campaigns ADD COLUMN solver_enabled BOOLEAN DEFAULT NULL",
        )


def _add_readiness_human_skipped(connection: Connection) -> None:
    # Ф2 #148: operator "Skip channel for this account" → a per-(account, channel)
    # human override the engine never selects. Default 0 so existing rows are unskipped.
    if "human_skipped" not in _sqlite_columns(connection, "neurocomment_readiness"):
        connection.exec_driver_sql(
            "ALTER TABLE neurocomment_readiness "
            "ADD COLUMN human_skipped INTEGER NOT NULL DEFAULT 0",
        )


def _add_campaign_account_channel(connection: Connection) -> None:
    # #25: optional per-account channel pin. NULL = all campaign channels (current
    # behaviour); a channel handle restricts the account to that one channel. The
    # account-link table is outside migration_steps._ALLOWED_TABLES, so the column
    # probe is inlined (a hard-coded table name, never user input) rather than routed
    # through _sqlite_columns.
    if not _sqlite_table_exists(connection, "neurocomment_campaign_accounts"):
        return
    rows = (
        connection.exec_driver_sql("PRAGMA table_info(neurocomment_campaign_accounts)")
        .mappings()
        .all()
    )
    if "channel" not in {str(row["name"]) for row in rows}:
        connection.exec_driver_sql(
            "ALTER TABLE neurocomment_campaign_accounts ADD COLUMN channel VARCHAR",
        )
