"""Shared SQLite plumbing — schema, engine, additive migrations, generic helpers.

This module owns the SQLAlchemy ``MetaData``, every table definition, engine
lifecycle, the additive-migration hook, and the small row/value helpers shared
across aggregates. The per-aggregate query functions live in
``core/repositories/<aggregate>.py`` (split out for #38); they import the table
objects and helpers below, and this module re-exports their public functions at
the bottom so existing ``from core.db import ...`` call sites keep working.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, cast

from sqlalchemy import (
    BigInteger,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    event,
)

from core.config import settings
from schemas.device_fingerprint import DeviceFingerprint, DevicePlatform

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from sqlalchemy.engine import Connection, Engine


_metadata = MetaData()
_device_fingerprints = Table(
    "device_fingerprints",
    _metadata,
    Column("account_id", String, primary_key=True),
    Column("platform", String, nullable=False),
    Column("device_model", String, nullable=False),
    Column("system_version", String, nullable=False),
    Column("app_version", String, nullable=False),
    Column("lang_code", String, nullable=False),
    Column("system_lang_code", String, nullable=False),
)
_accounts = Table(
    "accounts",
    _metadata,
    Column("account_id", String, primary_key=True),
    Column("label", String, nullable=True),
    Column("session_name", String, nullable=True),
    Column("status", String, nullable=False),
    Column("user_id", BigInteger, nullable=True),
    Column("phone", String, nullable=True),
    Column("username", String, nullable=True),
    Column("first_name", String, nullable=True),
    Column("last_name", String, nullable=True),
    Column("bio", String, nullable=True),
    Column("last_checked_at", String, nullable=True),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)
_account_proxies = Table(
    "account_proxies",
    _metadata,
    Column("account_id", String, ForeignKey("accounts.account_id"), primary_key=True),
    Column("proxy_type", String, nullable=False),
    Column("host", String, nullable=False),
    Column("port", Integer, nullable=False),
    Column("username", String, nullable=True),
    Column("password", String, nullable=True),
    Column("status", String, nullable=False),
    Column("last_checked_at", String, nullable=True),
    Column("last_error", String, nullable=True),
    Column("exit_ip", String, nullable=True),
    Column("country_code", String, nullable=True),
    Column("country_name", String, nullable=True),
    Column("asn", String, nullable=True),
    Column("is_datacenter", Integer, nullable=True),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)
_logs = Table(
    "logs",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", String, nullable=False),
    Column("level", String, nullable=False),
    Column("status", String, nullable=False),
    Column("account_id", String, nullable=True),
    Column("event", String, nullable=False),
    Column("extra", String, nullable=False),
)
_warming_channels = Table(
    "warming_channels",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("channel", String, nullable=False, unique=True),
    Column("label", String, nullable=True),
    Column("created_at", String, nullable=False),
)
_warming_settings = Table(
    "warming_settings",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("inter_account_chat", Integer, nullable=False),
    Column("reactions_enabled", Integer, nullable=False),
    Column("join_enabled", Integer, nullable=True),
    Column("enforce_readiness", Integer, nullable=True),
    Column("quiet_hours_enabled", Integer, nullable=True),
    Column("quiet_hours_start", Integer, nullable=True),
    Column("quiet_hours_end", Integer, nullable=True),
    Column("max_daily_actions", Integer, nullable=True),
    Column("gemini_api_key", String, nullable=False),
    Column("gemini_model", String, nullable=False),
    Column("updated_at", String, nullable=False),
)
_warming_account_state = Table(
    "warming_account_state",
    _metadata,
    Column("account_id", String, ForeignKey("accounts.account_id"), primary_key=True),
    Column("state", String, nullable=False),
    Column("cycles_completed", Integer, nullable=False),
    Column("last_event", String, nullable=True),
    Column("last_cycle_at", String, nullable=True),
    Column("next_run_at", String, nullable=True),
    Column("updated_at", String, nullable=False),
    Column("last_error", String, nullable=True),
    Column("last_action", String, nullable=True),
    Column("last_channel", String, nullable=True),
    Column("heartbeat_at", String, nullable=True),
    Column("started_at", String, nullable=True),
    Column("stopped_at", String, nullable=True),
    Column("flood_wait_seconds", Integer, nullable=True),
    Column("flood_wait_until", String, nullable=True),
    Column("proxy_snapshot", String, nullable=True),
    Column("daily_actions", Integer, nullable=True),
    Column("daily_count_date", String, nullable=True),
    Column("quarantine_count", Integer, nullable=True),
)
_account_spam_status = Table(
    "account_spam_status",
    _metadata,
    Column("account_id", String, ForeignKey("accounts.account_id"), primary_key=True),
    Column("status", String, nullable=False),
    Column("detail", String, nullable=True),
    Column("checked_at", String, nullable=False),
)


class _DatabaseState:
    engine: Engine | None = None
    database_path: Path | None = None


_state = _DatabaseState()


def configure_database(database_path: Path) -> None:
    if _state.engine is not None:
        _state.engine.dispose()
    _state.database_path = database_path
    _state.engine = None


def _get_engine() -> Engine:
    if _state.engine is None:
        database_path = _state.database_path or settings.db.path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            f"sqlite:///{database_path}",
            connect_args={"check_same_thread": False},
            future=True,
        )

        # SQLite ignores ForeignKey constraints unless PRAGMA foreign_keys is
        # set on every connection. WAL + busy_timeout + synchronous=NORMAL let
        # concurrent warming loops write without "database is locked".
        @event.listens_for(engine, "connect")
        def _configure_sqlite(dbapi_connection: Any, _connection_record: object) -> None:  # noqa: ANN401 - SQLAlchemy hands us the raw DBAPI handle.
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        _state.engine = engine
        _metadata.create_all(engine)
        _ensure_sqlite_schema(engine)
    return _state.engine


def _ensure_sqlite_schema(engine: Engine) -> None:
    """Tiny additive migration hook for local SQLite files created before new columns."""
    with engine.begin() as connection:
        account_columns = _sqlite_columns(connection, "accounts")
        if "bio" not in account_columns:
            connection.exec_driver_sql("ALTER TABLE accounts ADD COLUMN bio VARCHAR")
        proxy_columns = _sqlite_columns(connection, "account_proxies")
        _proxy_new_columns: tuple[tuple[str, str], ...] = (
            ("exit_ip", "VARCHAR"),
            ("country_code", "VARCHAR"),
            ("country_name", "VARCHAR"),
            ("asn", "VARCHAR"),
            ("is_datacenter", "INTEGER"),
        )
        for column_name, column_type in _proxy_new_columns:
            if column_name not in proxy_columns:
                connection.exec_driver_sql(
                    f"ALTER TABLE account_proxies ADD COLUMN {column_name} {column_type}",
                )
        warming_state_columns = _sqlite_columns(connection, "warming_account_state")
        # Each entry is (column_name, sql_type) — additive only, never destructive.
        _warming_state_new_columns: tuple[tuple[str, str], ...] = (
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
        for column_name, column_type in _warming_state_new_columns:
            if column_name not in warming_state_columns:
                connection.exec_driver_sql(
                    f"ALTER TABLE warming_account_state ADD COLUMN {column_name} {column_type}",
                )
        warming_settings_columns = _sqlite_columns(connection, "warming_settings")
        if "join_enabled" not in warming_settings_columns:
            # Default 1 (enabled) so accounts created before this column keep
            # joining channels — a NULL would otherwise read as "disabled".
            connection.exec_driver_sql(
                "ALTER TABLE warming_settings ADD COLUMN join_enabled INTEGER DEFAULT 1",
            )
        # User-editable warming controls promoted from config to the settings row.
        # Literal DEFAULTs match the config defaults so existing rows keep behaving.
        _warming_settings_new_columns: tuple[tuple[str, str], ...] = (
            ("enforce_readiness", "INTEGER DEFAULT 1"),
            ("quiet_hours_enabled", "INTEGER DEFAULT 0"),
            ("quiet_hours_start", "INTEGER DEFAULT 0"),
            ("quiet_hours_end", "INTEGER DEFAULT 0"),
            ("max_daily_actions", "INTEGER DEFAULT 0"),
        )
        for column_name, column_def in _warming_settings_new_columns:
            if column_name not in warming_settings_columns:
                connection.exec_driver_sql(
                    f"ALTER TABLE warming_settings ADD COLUMN {column_name} {column_def}",
                )


def _sqlite_columns(
    connection: Connection,
    table_name: Literal[
        "accounts",
        "account_proxies",
        "warming_account_state",
        "warming_settings",
    ],
) -> set[str]:
    # Whitelist of allowed table names — table_name is not user input but kept
    # narrow so ``exec_driver_sql`` can never see anything unexpected.
    allowed = ("accounts", "account_proxies", "warming_account_state", "warming_settings")
    if table_name not in allowed:
        msg = f"unsupported table {table_name!r}"
        raise ValueError(msg)
    rows = connection.exec_driver_sql(f"PRAGMA table_info({table_name})").mappings().all()
    return {str(row["name"]) for row in rows}


# --------------------------------------------------------------------------- #
# Generic row/value helpers shared by the repositories below.
# --------------------------------------------------------------------------- #
def _row_to_device_fingerprint(mapping: Mapping[str, object]) -> DeviceFingerprint:
    return DeviceFingerprint(
        account_id=str(mapping["account_id"]),
        platform=cast("DevicePlatform", mapping["platform"]),
        device_model=str(mapping["device_model"]),
        system_version=str(mapping["system_version"]),
        app_version=str(mapping["app_version"]),
        lang_code=str(mapping["lang_code"]),
        system_lang_code=str(mapping["system_lang_code"]),
    )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _required_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    msg = f"Expected integer-compatible value, got {type(value).__name__}"
    raise TypeError(msg)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(cast("int | str", value))


# --------------------------------------------------------------------------- #
# Domain repositories (#38) — split out of this module and re-exported so that
# existing ``from core.db import ...`` call sites keep working unchanged. These
# imports live at the bottom because the repositories import the table objects
# and helpers defined above.
# --------------------------------------------------------------------------- #
from core.repositories.accounts import (  # noqa: E402, F401
    create_account,
    delete_account_proxy,
    exit_ip_collisions,
    fetch_account,
    fetch_account_proxy,
    fetch_account_proxy_settings,
    list_accounts,
    update_account_from_session_check,
    update_account_profile_snapshot,
    update_account_proxy_check,
    upsert_account_proxy,
)
from core.repositories.content import (  # noqa: E402, F401
    record_sent_hash,
    try_reserve_sent_hash,
    was_hash_sent_since,
)
from core.repositories.device_fingerprint import (  # noqa: E402, F401
    fetch_device_fingerprint,
    insert_device_fingerprint,
    list_device_fingerprints,
)
from core.repositories.dialogues import (  # noqa: E402, F401
    count_pair_messages_since,
    latest_unreplied_for,
    list_dialogue_pairs,
    list_recent_dialogue_messages,
    mark_message_replied,
    pair_key,
    record_dialogue_message,
    replace_dialogue_pairs,
    try_claim_message_reply,
)
from core.repositories.logs import (  # noqa: E402, F401
    insert_log_row,
    list_filtered_logs,
    list_recent_logs,
)
from core.repositories.spam_status import (  # noqa: E402, F401
    get_spam_status,
    list_spam_statuses,
    upsert_spam_status,
)
from core.repositories.warming import (  # noqa: E402, F401
    add_warming_channel,
    fetch_warming_state,
    list_warming_channels,
    list_warming_states,
    load_warming_settings,
    remove_warming_channel,
    save_warming_settings,
    upsert_warming_state,
)
