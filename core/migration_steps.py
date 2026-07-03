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
        "neurocomment_runtime",
        "users",
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
    # ``account_proxies`` was retired by the proxy-pool migration (#18); on a
    # fresh DB the table no longer exists, so this legacy ALTER is a no-op.
    if not _sqlite_table_exists(connection, "account_proxies"):
        return
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


def _add_users_token_version(connection: Connection) -> None:
    # Session revocation (audit #2): a per-user monotonic counter carried in the
    # JWT ``ver`` claim. Logout bumps it, invalidating every outstanding token.
    # DEFAULT 0 backfills existing rows to the initial version.
    if not _sqlite_table_exists(connection, "users"):
        return
    if "token_version" not in _sqlite_columns(connection, "users"):
        connection.exec_driver_sql(
            "ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0",
        )


def _add_warming_settings_llm_columns(connection: Connection) -> None:
    # OpenAI captcha key/model + the operator's captcha-LLM provider choice.
    # Nullable → legacy rows read the config/.env fallback until the operator saves.
    # Guard the table so a hand-built legacy DB without it is a no-op, not an error.
    if not _sqlite_table_exists(connection, "warming_settings"):
        return
    columns = _sqlite_columns(connection, "warming_settings")
    if "openai_api_key" not in columns:
        connection.exec_driver_sql("ALTER TABLE warming_settings ADD COLUMN openai_api_key VARCHAR")
    if "openai_model" not in columns:
        connection.exec_driver_sql("ALTER TABLE warming_settings ADD COLUMN openai_model VARCHAR")
    if "captcha_llm_provider" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE warming_settings ADD COLUMN captcha_llm_provider VARCHAR",
        )


def _add_warming_state_promoted_to_nc(connection: Connection) -> None:
    # Operator graduation flag set from the warming card; default 0 keeps existing
    # rows opt-in (NC overview shows them only after explicit promotion).
    if "promoted_to_nc" not in _sqlite_columns(connection, "warming_account_state"):
        connection.exec_driver_sql(
            "ALTER TABLE warming_account_state "
            "ADD COLUMN promoted_to_nc INTEGER NOT NULL DEFAULT 0",
        )


def _add_warming_state_target_days(connection: Connection) -> None:
    # Operator-chosen warming duration (the start modal's day slider). NULL on
    # legacy rows / no explicit pick → the board falls back to warmed_min_days.
    if not _sqlite_table_exists(connection, "warming_account_state"):
        return
    if "target_days" not in _sqlite_columns(connection, "warming_account_state"):
        connection.exec_driver_sql(
            "ALTER TABLE warming_account_state ADD COLUMN target_days INTEGER",
        )


def _add_logs_indexes(connection: Connection) -> None:
    # audit #2: the logs table only had its autoincrement PK. The Logs page +
    # per-card panels filter by account_id ordering id DESC, and the retention
    # sweep filters created_at — both full-scanned the append-only table. Mirror
    # of the Index(...) entries on core.db._logs so create_all'd fresh DBs match.
    # On the app path create_all builds ``logs`` before migrations run; guard the
    # table so a hand-built legacy DB without it is a no-op, not an error.
    if not _sqlite_table_exists(connection, "logs"):
        return
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_logs_account_id ON logs(account_id, id)",
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_logs_created_at ON logs(created_at)",
    )


def _sqlite_table_exists(connection: Connection, table_name: str) -> bool:
    row = connection.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).first()
    return row is not None


# The neurocomment schema bodies live in a sibling module for the file-size
# budget; re-imported here (at the bottom, after the generic helpers they use are
# defined) so ``core.migrations`` keeps importing every step from this module.
from core.migration_steps_neurocomment import (  # noqa: E402, F401
    _add_neurocomment_challenges,
    _add_neurocomment_comment_indexes,
    _add_neurocomment_runtime,
    _add_neurocomment_settings,
    _add_neurocomment_tables,
    _add_readiness_human_skipped,
)
