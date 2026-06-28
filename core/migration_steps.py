"""Migration step bodies — the individual schema changes.

Split out of ``core.migrations`` so that module stays the append-only registry +
runner (the single source of truth for ordering). Each body is idempotent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


_ALLOWED_TABLES = frozenset(
    {
        "accounts",
        "account_proxies",
        "warming_account_state",
        "warming_settings",
        "neurocomment_campaigns",
        "neurocomment_readiness",
    },
)


def _sqlite_columns(connection: Connection, table_name: str) -> set[str]:
    # ``table_name`` is never user input but kept on a whitelist so a future
    # caller can not accidentally interpolate something unexpected.
    if table_name not in _ALLOWED_TABLES:
        msg = f"unsupported table {table_name!r}"
        raise ValueError(msg)
    rows = connection.exec_driver_sql(f"PRAGMA table_info({table_name})").mappings().all()
    return {str(row["name"]) for row in rows}


def _add_account_bio(connection: Connection) -> None:
    if "bio" not in _sqlite_columns(connection, "accounts"):
        connection.exec_driver_sql("ALTER TABLE accounts ADD COLUMN bio VARCHAR")


def _add_account_proxy_geo(connection: Connection) -> None:
    proxy_columns = _sqlite_columns(connection, "account_proxies")
    new_columns: tuple[tuple[str, str], ...] = (
        ("exit_ip", "VARCHAR"),
        ("country_code", "VARCHAR"),
        ("country_name", "VARCHAR"),
        ("asn", "VARCHAR"),
        ("is_datacenter", "INTEGER"),
    )
    for column_name, column_type in new_columns:
        if column_name not in proxy_columns:
            connection.exec_driver_sql(
                f"ALTER TABLE account_proxies ADD COLUMN {column_name} {column_type}",
            )


def _add_warming_state_runtime_columns(connection: Connection) -> None:
    existing = _sqlite_columns(connection, "warming_account_state")
    new_columns: tuple[tuple[str, str], ...] = (
        ("last_error", "VARCHAR"),
        ("last_action", "VARCHAR"),
        ("last_channel", "VARCHAR"),
        ("heartbeat_at", "VARCHAR"),
        ("started_at", "VARCHAR"),
        ("stopped_at", "VARCHAR"),
        ("flood_wait_seconds", "INTEGER"),
        ("flood_wait_until", "VARCHAR"),
        ("proxy_snapshot", "VARCHAR"),
        ("daily_actions", "INTEGER"),
        ("daily_count_date", "VARCHAR"),
        ("quarantine_count", "INTEGER"),
    )
    for column_name, column_type in new_columns:
        if column_name not in existing:
            connection.exec_driver_sql(
                f"ALTER TABLE warming_account_state ADD COLUMN {column_name} {column_type}",
            )


def _add_warming_join_enabled(connection: Connection) -> None:
    if "join_enabled" not in _sqlite_columns(connection, "warming_settings"):
        # Default 1 (enabled) so accounts created before this column keep
        # joining channels — a NULL would otherwise read as "disabled".
        connection.exec_driver_sql(
            "ALTER TABLE warming_settings ADD COLUMN join_enabled INTEGER DEFAULT 1",
        )


def _add_warming_user_controls(connection: Connection) -> None:
    existing = _sqlite_columns(connection, "warming_settings")
    new_columns: tuple[tuple[str, str], ...] = (
        ("enforce_readiness", "INTEGER DEFAULT 1"),
        ("quiet_hours_enabled", "INTEGER DEFAULT 0"),
        ("quiet_hours_start", "INTEGER DEFAULT 0"),
        ("quiet_hours_end", "INTEGER DEFAULT 0"),
        ("max_daily_actions", "INTEGER DEFAULT 0"),
    )
    for column_name, column_def in new_columns:
        if column_name not in existing:
            connection.exec_driver_sql(
                f"ALTER TABLE warming_settings ADD COLUMN {column_name} {column_def}",
            )


def _add_warming_joined_channels(connection: Connection) -> None:
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS warming_joined_channels ("
        "  account_id VARCHAR NOT NULL,"
        "  channel VARCHAR NOT NULL,"
        "  created_at VARCHAR NOT NULL,"
        "  PRIMARY KEY (account_id, channel)"
        ")"
    )


def _add_unique_session_name_index(connection: Connection) -> None:
    # F5: forbid two accounts from sharing a Telethon .session file.
    # SQLite treats NULLs as distinct in a UNIQUE index, so NULL session_names
    # remain free to coexist for accounts that don't override the path.
    #
    # Legacy databases predating this migration may already contain duplicate
    # session_name rows. Creating the index naively would raise IntegrityError
    # on every startup. Auto-remediate by keeping the oldest row per
    # session_name and nulling the rest, logging which accounts were touched
    # so the operator can clean up later.
    #
    # Round-4 P2.3: when we null a row's session_name, its session file path
    # silently changes (``_session_path`` falls back to ``account_id`` when
    # session_name is None). Leaving ``status='alive'`` would mean the next
    # runtime action opens a non-existent / different session — better to
    # mark the row ``new`` so the operator re-runs the session check before
    # we trust it again.
    rows = connection.exec_driver_sql(
        "SELECT account_id, session_name FROM accounts "
        "WHERE session_name IS NOT NULL "
        "ORDER BY session_name, created_at, account_id",
    ).all()
    seen: set[str] = set()
    nulled: list[tuple[str, str]] = []
    for account_id, session_name in rows:
        name = str(session_name)
        if name in seen:
            nulled.append((str(account_id), name))
            continue
        seen.add(name)
    applied_at = datetime.now(UTC).isoformat()
    for account_id, _name in nulled:
        connection.exec_driver_sql(
            "UPDATE accounts "
            "SET session_name = NULL, status = 'new', updated_at = ? "
            "WHERE account_id = ?",
            (applied_at, account_id),
        )
    if nulled:
        # No direct dependency on core.logging here (migrations are import-light);
        # operator visibility comes via the schema_version row + this audit table.
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_remediations ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  migration INTEGER NOT NULL,"
            "  account_id VARCHAR NOT NULL,"
            "  detail VARCHAR NOT NULL,"
            "  applied_at VARCHAR NOT NULL"
            ")",
        )
        for account_id, name in nulled:
            connection.exec_driver_sql(
                "INSERT INTO schema_remediations (migration, account_id, detail, applied_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    7,
                    account_id,
                    f"session_name {name!r} nulled (duplicate); status -> 'new'",
                    applied_at,
                ),
            )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_accounts_session_name_unique "
        "ON accounts(session_name)",
    )


def _add_warming_state_run_id(connection: Connection) -> None:
    # P1.2: per-loop generation marker so an old in-flight cycle cannot write
    # through after a new start_warming has minted a fresh run_id.
    if "run_id" not in _sqlite_columns(connection, "warming_account_state"):
        connection.exec_driver_sql(
            "ALTER TABLE warming_account_state ADD COLUMN run_id VARCHAR",
        )


def _rename_proxy_type_http_to_https(connection: Connection) -> None:
    if "proxy_type" not in _sqlite_columns(connection, "account_proxies"):
        return
    connection.exec_driver_sql(
        "UPDATE account_proxies SET proxy_type = 'https' WHERE proxy_type = 'http'",
    )


def _add_warming_phase_columns(connection: Connection) -> None:
    existing = _sqlite_columns(connection, "warming_account_state")
    if "current_phase" not in existing:
        connection.exec_driver_sql(
            "ALTER TABLE warming_account_state ADD COLUMN current_phase VARCHAR",
        )
    if "phase_entered_at" not in existing:
        connection.exec_driver_sql(
            "ALTER TABLE warming_account_state ADD COLUMN phase_entered_at VARCHAR",
        )


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


def _add_users_table(connection: Connection) -> None:
    # Auth (#168): admin-seeded users; ``role`` from day one (no RBAC yet).
    # username is UNIQUE so a duplicate login name can't be created.
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS users ("
        "  id VARCHAR PRIMARY KEY,"
        "  username VARCHAR NOT NULL UNIQUE,"
        "  password_hash VARCHAR NOT NULL,"
        "  role VARCHAR NOT NULL,"
        "  created_at VARCHAR NOT NULL,"
        "  updated_at VARCHAR NOT NULL"
        ")",
    )


def _add_warming_state_promoted_to_nc(connection: Connection) -> None:
    # Operator graduation flag set from the warming card; default 0 keeps existing
    # rows opt-in (NC overview shows them only after explicit promotion).
    if "promoted_to_nc" not in _sqlite_columns(connection, "warming_account_state"):
        connection.exec_driver_sql(
            "ALTER TABLE warming_account_state "
            "ADD COLUMN promoted_to_nc INTEGER NOT NULL DEFAULT 0",
        )
