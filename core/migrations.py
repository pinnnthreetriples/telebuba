"""Tiny versioned SQLite migration registry.

Replaces the open-ended ``_ensure_sqlite_schema`` block: every schema change is
a numbered, named migration that runs at most once per database. State lives in
a ``schema_version`` table (one row per applied migration) so we can audit what
ran and when, and so new migrations can be appended without re-doing the older
ones.

Constraints — deliberately small:

- No Alembic, no autogen. We are still a single-file SQLite app and the cost
  of a real migration tool outweighs the value. The moment we add Postgres or
  branch-merging migrations, swap this for Alembic.
- Each migration MUST be idempotent (``ADD COLUMN`` guarded by a column-name
  check). Older databases predating the registry already have some columns;
  the guards let us stamp those migrations as applied without failing on
  "duplicate column".
- Append-only. Never edit or delete a migration in place — write a new one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Connection, Engine

    _Migration = Callable[[Connection], None]


_ALLOWED_TABLES = frozenset(
    {
        "accounts",
        "account_proxies",
        "warming_account_state",
        "warming_settings",
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


# Append-only registry. ``version`` is the canonical identifier and must never
# be reused; ``name`` is informational and surfaces in the audit table.
MIGRATIONS: tuple[tuple[int, str, _Migration], ...] = (
    (1, "add_account_bio", _add_account_bio),
    (2, "add_account_proxy_geo", _add_account_proxy_geo),
    (3, "add_warming_state_runtime_columns", _add_warming_state_runtime_columns),
    (4, "add_warming_join_enabled", _add_warming_join_enabled),
    (5, "add_warming_user_controls", _add_warming_user_controls),
    (6, "add_warming_joined_channels", _add_warming_joined_channels),
    (7, "add_unique_session_name_index", _add_unique_session_name_index),
    (8, "add_warming_state_run_id", _add_warming_state_run_id),
    (9, "rename_proxy_type_http_to_https", _rename_proxy_type_http_to_https),
    (10, "add_warming_phase_columns", _add_warming_phase_columns),
)


def _ensure_schema_version_table(connection: Connection) -> None:
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "  version INTEGER PRIMARY KEY,"
        "  name VARCHAR NOT NULL,"
        "  applied_at VARCHAR NOT NULL"
        ")",
    )


def _applied_versions(connection: Connection) -> set[int]:
    rows = connection.exec_driver_sql("SELECT version FROM schema_version").all()
    return {int(row[0]) for row in rows}


def apply_migrations(engine: Engine) -> None:
    """Run every migration that has not yet been stamped on this database.

    Idempotent: a fresh database where ``create_all`` already produced every
    column still gets each migration row inserted (the ``ADD COLUMN`` guards
    make the bodies no-ops). Re-running the same registry on the same DB does
    nothing.
    """
    with engine.begin() as connection:
        _ensure_schema_version_table(connection)
        applied = _applied_versions(connection)
        for version, name, body in MIGRATIONS:
            if version in applied:
                continue
            body(connection)
            connection.exec_driver_sql(
                "INSERT INTO schema_version(version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, datetime.now(UTC).isoformat()),
            )
