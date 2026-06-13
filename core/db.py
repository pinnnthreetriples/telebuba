from __future__ import annotations

import asyncio
import json
from contextlib import suppress
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
    delete,
    event,
    insert,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError

from core.config import settings
from schemas.accounts import (
    AccountCreate,
    AccountList,
    AccountProfileUpdateRequest,
    AccountRead,
    AccountStatus,
)
from schemas.device_fingerprint import DeviceFingerprint, DevicePlatform
from schemas.logs import LogEntry, LogEventInput, LogFilter, LogLevel, LogStatus
from schemas.proxy import (
    AccountProxyCheckUpdate,
    AccountProxyDelete,
    AccountProxyRead,
    AccountProxySettings,
    AccountProxyUpsert,
    ProxyStatus,
    ProxyType,
)
from schemas.warming import (
    WarmingChannel,
    WarmingChannelList,
    WarmingSettingsSecret,
    WarmingState,
    WarmingStateRecord,
    WarmingStateWrite,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from sqlalchemy.engine import Connection, Engine
    from sqlalchemy.sql import Select

    from schemas.telegram_session import TelegramSessionCheckResult


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

_WARMING_SETTINGS_ID = 1


class _DatabaseState:
    engine: Engine | None = None
    database_path: Path | None = None


_state = _DatabaseState()
_MASK_PASSTHROUGH_LENGTH = 2


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

        # SQLite ignores ForeignKey constraints unless this PRAGMA is set on
        # every connection. Without it, orphan rows are silently allowed.
        @event.listens_for(engine, "connect")
        def _enable_sqlite_fk(dbapi_connection: Any, _connection_record: object) -> None:  # noqa: ANN401 - SQLAlchemy hands us the raw DBAPI handle.
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
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


def _row_to_account(mapping: Mapping[str, object]) -> AccountRead:
    return AccountRead(
        account_id=str(mapping["account_id"]),
        label=_optional_str(mapping.get("label")),
        session_name=_optional_str(mapping.get("session_name")),
        status=cast("AccountStatus", mapping["status"]),
        user_id=_optional_int(mapping.get("user_id")),
        phone=_optional_str(mapping.get("phone")),
        username=_optional_str(mapping.get("username")),
        first_name=_optional_str(mapping.get("first_name")),
        last_name=_optional_str(mapping.get("last_name")),
        bio=_optional_str(mapping.get("bio")),
        last_checked_at=_optional_str(mapping.get("last_checked_at")),
        created_at=str(mapping["created_at"]),
        updated_at=str(mapping["updated_at"]),
        device_platform=_optional_str(mapping.get("device_platform")),
        device_model=_optional_str(mapping.get("device_model")),
        device_system_version=_optional_str(mapping.get("device_system_version")),
        device_app_version=_optional_str(mapping.get("device_app_version")),
        proxy_type=_optional_str(mapping.get("proxy_type")),
        proxy_host=_optional_str(mapping.get("proxy_host")),
        proxy_port=_optional_int(mapping.get("proxy_port")),
        proxy_status=_optional_str(mapping.get("proxy_status")),
        proxy_last_checked_at=_optional_str(mapping.get("proxy_last_checked_at")),
        proxy_last_error=_optional_str(mapping.get("proxy_last_error")),
        proxy_exit_ip=_optional_str(mapping.get("proxy_exit_ip")),
        proxy_country_code=_optional_str(mapping.get("proxy_country_code")),
        proxy_country_name=_optional_str(mapping.get("proxy_country_name")),
    )


def _row_to_account_proxy(mapping: Mapping[str, object]) -> AccountProxyRead:
    return AccountProxyRead(
        account_id=str(mapping["account_id"]),
        proxy_type=cast("ProxyType", mapping["proxy_type"]),
        host=str(mapping["host"]),
        port=_required_int(mapping["port"]),
        username=_mask_username(_optional_str(mapping.get("username"))),
        has_password=bool(mapping.get("password")),
        status=cast("ProxyStatus", mapping["status"]),
        last_checked_at=_optional_str(mapping.get("last_checked_at")),
        last_error=_optional_str(mapping.get("last_error")),
        exit_ip=_optional_str(mapping.get("exit_ip")),
        country_code=_optional_str(mapping.get("country_code")),
        country_name=_optional_str(mapping.get("country_name")),
        asn=_optional_str(mapping.get("asn")),
        is_datacenter=bool(mapping.get("is_datacenter")),
        updated_at=str(mapping["updated_at"]),
    )


def _row_to_account_proxy_settings(mapping: Mapping[str, object]) -> AccountProxySettings:
    return AccountProxySettings(
        account_id=str(mapping["account_id"]),
        proxy_type=cast("ProxyType", mapping["proxy_type"]),
        host=str(mapping["host"]),
        port=_required_int(mapping["port"]),
        username=_optional_str(mapping.get("username")),
        password=_optional_str(mapping.get("password")),
    )


def _account_select_statement() -> Select[tuple[Any, ...]]:
    return select(
        _accounts.c.account_id,
        _accounts.c.label,
        _accounts.c.session_name,
        _accounts.c.status,
        _accounts.c.user_id,
        _accounts.c.phone,
        _accounts.c.username,
        _accounts.c.first_name,
        _accounts.c.last_name,
        _accounts.c.bio,
        _accounts.c.last_checked_at,
        _accounts.c.created_at,
        _accounts.c.updated_at,
        _device_fingerprints.c.platform.label("device_platform"),
        _device_fingerprints.c.device_model.label("device_model"),
        _device_fingerprints.c.system_version.label("device_system_version"),
        _device_fingerprints.c.app_version.label("device_app_version"),
        _account_proxies.c.proxy_type.label("proxy_type"),
        _account_proxies.c.host.label("proxy_host"),
        _account_proxies.c.port.label("proxy_port"),
        _account_proxies.c.status.label("proxy_status"),
        _account_proxies.c.last_checked_at.label("proxy_last_checked_at"),
        _account_proxies.c.last_error.label("proxy_last_error"),
        _account_proxies.c.exit_ip.label("proxy_exit_ip"),
        _account_proxies.c.country_code.label("proxy_country_code"),
        _account_proxies.c.country_name.label("proxy_country_name"),
    ).select_from(
        _accounts.outerjoin(
            _device_fingerprints,
            _accounts.c.account_id == _device_fingerprints.c.account_id,
        ).outerjoin(
            _account_proxies,
            _accounts.c.account_id == _account_proxies.c.account_id,
        ),
    )


def _fetch_account(account_id: str) -> AccountRead | None:
    statement = _account_select_statement().where(_accounts.c.account_id == account_id)
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    if row is None:
        return None
    return _row_to_account(cast("Mapping[str, object]", row))


async def fetch_account(account_id: str) -> AccountRead | None:
    return await asyncio.to_thread(_fetch_account, account_id)


def _create_account(data: AccountCreate) -> AccountRead:
    now = _now_iso()
    values = {
        "account_id": data.account_id,
        "label": data.label,
        "session_name": data.session_name,
        "status": "new",
        "created_at": now,
        "updated_at": now,
    }
    with _get_engine().begin() as connection, suppress(IntegrityError):
        connection.execute(insert(_accounts).values(**values))
    account = _fetch_account(data.account_id)
    if account is None:
        msg = f"Account was not persisted: {data.account_id}"
        raise RuntimeError(msg)
    return account


async def create_account(data: AccountCreate) -> AccountRead:
    return await asyncio.to_thread(_create_account, data)


def _list_accounts() -> AccountList:
    statement = _account_select_statement().order_by(_accounts.c.created_at.desc())
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return AccountList(
        accounts=[_row_to_account(cast("Mapping[str, object]", row)) for row in rows],
    )


async def list_accounts() -> AccountList:
    return await asyncio.to_thread(_list_accounts)


def _fetch_account_proxy(account_id: str) -> AccountProxyRead | None:
    statement = select(_account_proxies).where(_account_proxies.c.account_id == account_id)
    with _get_engine().begin() as connection:
        row = connection.execute(statement).mappings().first()
    if row is None:
        return None
    return _row_to_account_proxy(cast("Mapping[str, object]", row))


async def fetch_account_proxy(account_id: str) -> AccountProxyRead | None:
    return await asyncio.to_thread(_fetch_account_proxy, account_id)


def _fetch_account_proxy_settings(account_id: str) -> AccountProxySettings | None:
    statement = select(_account_proxies).where(_account_proxies.c.account_id == account_id)
    with _get_engine().begin() as connection:
        row = connection.execute(statement).mappings().first()
    if row is None:
        return None
    return _row_to_account_proxy_settings(cast("Mapping[str, object]", row))


async def fetch_account_proxy_settings(account_id: str) -> AccountProxySettings | None:
    return await asyncio.to_thread(_fetch_account_proxy_settings, account_id)


def _upsert_account_proxy(data: AccountProxyUpsert) -> AccountProxyRead:
    if _fetch_account(data.account_id) is None:
        msg = f"Account not found: {data.account_id}"
        raise ValueError(msg)
    now = _now_iso()
    values: dict[str, object | None] = {
        "proxy_type": data.proxy_type,
        "host": data.host.strip(),
        "port": data.port,
        "username": data.username.strip() if data.username else None,
        "status": "unknown",
        "last_checked_at": None,
        "last_error": None,
        "exit_ip": None,
        "country_code": None,
        "country_name": None,
        "updated_at": now,
    }
    existing = _fetch_account_proxy_settings(data.account_id)
    with _get_engine().begin() as connection:
        if existing is None:
            connection.execute(
                insert(_account_proxies).values(
                    account_id=data.account_id,
                    password=data.password,
                    created_at=now,
                    **values,
                ),
            )
        else:
            if data.password is not None:
                values["password"] = data.password
            connection.execute(
                update(_account_proxies)
                .where(_account_proxies.c.account_id == data.account_id)
                .values(**values),
            )
    proxy = _fetch_account_proxy(data.account_id)
    if proxy is None:
        msg = f"Proxy was not persisted: {data.account_id}"
        raise RuntimeError(msg)
    return proxy


async def upsert_account_proxy(data: AccountProxyUpsert) -> AccountProxyRead:
    return await asyncio.to_thread(_upsert_account_proxy, data)


def _delete_account_proxy(data: AccountProxyDelete) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            delete(_account_proxies).where(_account_proxies.c.account_id == data.account_id),
        )


async def delete_account_proxy(data: AccountProxyDelete) -> None:
    await asyncio.to_thread(_delete_account_proxy, data)


def _exit_ip_collisions() -> dict[str, list[str]]:
    statement = select(
        _account_proxies.c.account_id,
        _account_proxies.c.exit_ip,
    ).where(_account_proxies.c.exit_ip.is_not(None))
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).all()
    grouped: dict[str, list[str]] = {}
    for account_id, exit_ip in rows:
        grouped.setdefault(str(exit_ip), []).append(str(account_id))
    return {ip: ids for ip, ids in grouped.items() if len(ids) > 1}


async def exit_ip_collisions() -> dict[str, list[str]]:
    """Map each shared exit IP to the accounts using it (only IPs used by 2+)."""
    return await asyncio.to_thread(_exit_ip_collisions)


def _update_account_proxy_check(data: AccountProxyCheckUpdate) -> AccountProxyRead:
    now = _now_iso()
    values: dict[str, object | None] = {
        "status": data.status,
        "last_checked_at": now,
        "last_error": data.last_error,
        "exit_ip": data.exit_ip,
        "country_code": data.country_code,
        "country_name": data.country_name,
        "asn": data.asn,
        "is_datacenter": int(data.is_datacenter),
        "updated_at": now,
    }
    with _get_engine().begin() as connection:
        result = connection.execute(
            update(_account_proxies)
            .where(_account_proxies.c.account_id == data.account_id)
            .values(**values),
        )
    if result.rowcount == 0:
        msg = f"Proxy not found for account: {data.account_id}"
        raise ValueError(msg)
    proxy = _fetch_account_proxy(data.account_id)
    if proxy is None:
        msg = f"Proxy not found for account: {data.account_id}"
        raise ValueError(msg)
    return proxy


async def update_account_proxy_check(data: AccountProxyCheckUpdate) -> AccountProxyRead:
    return await asyncio.to_thread(_update_account_proxy_check, data)


def _update_account_profile_snapshot(data: AccountProfileUpdateRequest) -> AccountRead:
    values: dict[str, object | None] = {
        "first_name": data.first_name,
        "updated_at": _now_iso(),
    }
    if data.last_name is not None:
        values["last_name"] = data.last_name
    if data.username is not None:
        values["username"] = data.username
    if data.bio is not None:
        values["bio"] = data.bio
    with _get_engine().begin() as connection:
        result = connection.execute(
            update(_accounts).where(_accounts.c.account_id == data.account_id).values(**values),
        )
    if result.rowcount == 0:
        msg = f"Account not found: {data.account_id}"
        raise ValueError(msg)
    account = _fetch_account(data.account_id)
    if account is None:
        msg = f"Account not found: {data.account_id}"
        raise ValueError(msg)
    return account


async def update_account_profile_snapshot(data: AccountProfileUpdateRequest) -> AccountRead:
    return await asyncio.to_thread(_update_account_profile_snapshot, data)


def _mask_username(username: str | None) -> str | None:
    if not username:
        return None
    if len(username) <= _MASK_PASSTHROUGH_LENGTH:
        return f"{username[0]}*"
    return f"{username[0]}***{username[-1]}"


def _update_account_from_session_check(result: TelegramSessionCheckResult) -> AccountRead:
    now = _now_iso()
    values: dict[str, object] = {
        "status": result.status,
        "last_checked_at": now,
        "updated_at": now,
    }
    if result.status == "alive":
        values.update(
            {
                "user_id": result.user_id,
                "phone": result.phone,
                "username": result.username,
                "first_name": result.first_name,
                "last_name": result.last_name,
            },
        )

    with _get_engine().begin() as connection:
        connection.execute(
            update(_accounts).where(_accounts.c.account_id == result.account_id).values(**values),
        )

    account = _fetch_account(result.account_id)
    if account is None:
        msg = f"Account not found: {result.account_id}"
        raise RuntimeError(msg)
    return account


async def update_account_from_session_check(result: TelegramSessionCheckResult) -> AccountRead:
    return await asyncio.to_thread(_update_account_from_session_check, result)


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


_STATUS_BY_LEVEL: dict[LogLevel, LogStatus] = {
    "INFO": "success",
    "WARNING": "warning",
    "ERROR": "error",
}


def _insert_log_row(event: LogEventInput) -> LogEntry:
    values = {
        "created_at": _now_iso(),
        "level": event.level,
        "status": _STATUS_BY_LEVEL[event.level],
        "account_id": event.account_id,
        "event": event.event,
        "extra": json.dumps(event.extra, default=str, sort_keys=True),
    }
    with _get_engine().begin() as connection:
        result = connection.execute(insert(_logs).values(**values))
    primary_key = result.inserted_primary_key
    if primary_key is None:
        msg = "Insert into logs returned no primary key"
        raise RuntimeError(msg)
    inserted_id = int(primary_key[0])
    return LogEntry(
        id=inserted_id,
        created_at=str(values["created_at"]),
        level=event.level,
        status=_STATUS_BY_LEVEL[event.level],
        account_id=event.account_id,
        event=event.event,
        extra=event.extra,
    )


async def insert_log_row(event: LogEventInput) -> LogEntry:
    """Persist one log event into the SQLite ``logs`` table and return the row."""
    return await asyncio.to_thread(_insert_log_row, event)


def _list_recent_logs(limit: int) -> list[LogEntry]:
    statement = select(_logs).order_by(_logs.c.id.desc()).limit(limit)
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    entries: list[LogEntry] = []
    for row in rows:
        raw_extra = row["extra"]
        extra: dict[str, object] = json.loads(raw_extra) if raw_extra else {}
        entries.append(
            LogEntry(
                id=int(cast("int | str", row["id"])),
                created_at=str(row["created_at"]),
                level=cast("LogLevel", row["level"]),
                status=cast("LogStatus", row["status"]),
                account_id=_optional_str(row["account_id"]),
                event=str(row["event"]),
                extra=extra,
            ),
        )
    return entries


async def list_recent_logs(limit: int = 100) -> list[LogEntry]:
    """Return the latest log entries (newest first). Used by the future Logs page."""
    return await asyncio.to_thread(_list_recent_logs, limit)


def _list_filtered_logs(log_filter: LogFilter) -> list[LogEntry]:
    statement = select(_logs).order_by(_logs.c.id.desc()).limit(log_filter.limit)
    if log_filter.status != "all":
        statement = statement.where(_logs.c.status == log_filter.status)
    if log_filter.account_id:
        statement = statement.where(_logs.c.account_id == log_filter.account_id)
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    entries: list[LogEntry] = []
    for row in rows:
        raw_extra = row["extra"]
        extra: dict[str, object] = json.loads(raw_extra) if raw_extra else {}
        entries.append(
            LogEntry(
                id=int(cast("int | str", row["id"])),
                created_at=str(row["created_at"]),
                level=cast("LogLevel", row["level"]),
                status=cast("LogStatus", row["status"]),
                account_id=_optional_str(row["account_id"]),
                event=str(row["event"]),
                extra=extra,
            ),
        )
    return entries


async def list_filtered_logs(log_filter: LogFilter) -> list[LogEntry]:
    """Return the latest log entries that match the filter (newest first)."""
    return await asyncio.to_thread(_list_filtered_logs, log_filter)


# --------------------------------------------------------------------------- #
# Warming — channels, settings (single row), per-account state.
# --------------------------------------------------------------------------- #


def _row_to_warming_channel(mapping: Mapping[str, object]) -> WarmingChannel:
    return WarmingChannel(
        channel=str(mapping["channel"]),
        label=_optional_str(mapping.get("label")),
        created_at=str(mapping["created_at"]),
    )


def _list_warming_channels() -> WarmingChannelList:
    statement = select(_warming_channels).order_by(_warming_channels.c.id.asc())
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return WarmingChannelList(
        channels=[_row_to_warming_channel(cast("Mapping[str, object]", row)) for row in rows],
    )


async def list_warming_channels() -> WarmingChannelList:
    return await asyncio.to_thread(_list_warming_channels)


def _add_warming_channel(channel: str, label: str | None) -> WarmingChannelList:
    with _get_engine().begin() as connection, suppress(IntegrityError):
        connection.execute(
            insert(_warming_channels).values(
                channel=channel,
                label=label,
                created_at=_now_iso(),
            ),
        )
    return _list_warming_channels()


async def add_warming_channel(channel: str, label: str | None = None) -> WarmingChannelList:
    """Insert a channel (ignored if it already exists) and return the full list."""
    return await asyncio.to_thread(_add_warming_channel, channel, label)


def _remove_warming_channel(channel: str) -> WarmingChannelList:
    with _get_engine().begin() as connection:
        connection.execute(
            delete(_warming_channels).where(_warming_channels.c.channel == channel),
        )
    return _list_warming_channels()


async def remove_warming_channel(channel: str) -> WarmingChannelList:
    return await asyncio.to_thread(_remove_warming_channel, channel)


def _bool_or(value: object, default: bool) -> bool:  # noqa: FBT001
    return default if value is None else bool(value)


def _int_or(value: object, default: int) -> int:
    return default if value is None else int(cast("int | str", value))


def _row_to_warming_settings_secret(mapping: Mapping[str, object]) -> WarmingSettingsSecret:
    # Columns added after the row was first created are nullable; a NULL means
    # "never set", so fall back to the config default to preserve old behaviour.
    warm = settings.warming
    return WarmingSettingsSecret(
        inter_account_chat=bool(mapping["inter_account_chat"]),
        reactions_enabled=bool(mapping["reactions_enabled"]),
        join_enabled=_bool_or(mapping.get("join_enabled"), default=True),
        enforce_readiness=_bool_or(mapping.get("enforce_readiness"), warm.enforce_readiness),
        quiet_hours_enabled=_bool_or(mapping.get("quiet_hours_enabled"), warm.quiet_hours_enabled),
        quiet_hours_start=_int_or(mapping.get("quiet_hours_start"), warm.quiet_hours_start),
        quiet_hours_end=_int_or(mapping.get("quiet_hours_end"), warm.quiet_hours_end),
        max_daily_actions=_int_or(mapping.get("max_daily_actions"), warm.max_daily_actions),
        gemini_api_key=str(mapping["gemini_api_key"]),
        gemini_model=str(mapping["gemini_model"]),
        updated_at=str(mapping["updated_at"]),
    )


def _default_warming_settings_values() -> dict[str, object]:
    warm = settings.warming
    return {
        "id": _WARMING_SETTINGS_ID,
        "inter_account_chat": 0,
        "reactions_enabled": 1,
        "join_enabled": 1,
        "enforce_readiness": int(warm.enforce_readiness),
        "quiet_hours_enabled": int(warm.quiet_hours_enabled),
        "quiet_hours_start": warm.quiet_hours_start,
        "quiet_hours_end": warm.quiet_hours_end,
        "max_daily_actions": warm.max_daily_actions,
        "gemini_api_key": settings.gemini.api_key,
        "gemini_model": settings.gemini.model,
        "updated_at": _now_iso(),
    }


def _load_warming_settings() -> WarmingSettingsSecret:
    statement = select(_warming_settings).where(_warming_settings.c.id == _WARMING_SETTINGS_ID)
    with _get_engine().begin() as connection:
        row = connection.execute(statement).mappings().first()
        if row is None:
            values = _default_warming_settings_values()
            connection.execute(insert(_warming_settings).values(**values))
            return _row_to_warming_settings_secret(cast("Mapping[str, object]", values))
    return _row_to_warming_settings_secret(cast("Mapping[str, object]", row))


async def load_warming_settings() -> WarmingSettingsSecret:
    """Return the singleton warming settings row, creating defaults on first read."""
    return await asyncio.to_thread(_load_warming_settings)


def _save_warming_settings(  # noqa: PLR0913 - one explicit column per setting reads clearer.
    *,
    inter_account_chat: bool,
    reactions_enabled: bool,
    join_enabled: bool = True,
    enforce_readiness: bool = True,
    quiet_hours_enabled: bool = False,
    quiet_hours_start: int = 0,
    quiet_hours_end: int = 0,
    max_daily_actions: int = 0,
    gemini_api_key: str | None,
    gemini_model: str | None = None,
) -> WarmingSettingsSecret:
    current = _load_warming_settings()
    new_key = current.gemini_api_key if gemini_api_key is None else gemini_api_key
    new_model = gemini_model or current.gemini_model
    values: dict[str, object] = {
        "inter_account_chat": int(inter_account_chat),
        "reactions_enabled": int(reactions_enabled),
        "join_enabled": int(join_enabled),
        "enforce_readiness": int(enforce_readiness),
        "quiet_hours_enabled": int(quiet_hours_enabled),
        "quiet_hours_start": quiet_hours_start,
        "quiet_hours_end": quiet_hours_end,
        "max_daily_actions": max_daily_actions,
        "gemini_api_key": new_key,
        "gemini_model": new_model,
        "updated_at": _now_iso(),
    }
    with _get_engine().begin() as connection:
        connection.execute(
            update(_warming_settings)
            .where(_warming_settings.c.id == _WARMING_SETTINGS_ID)
            .values(**values),
        )
    return _load_warming_settings()


async def save_warming_settings(  # noqa: PLR0913 - mirrors the explicit column list.
    *,
    inter_account_chat: bool,
    reactions_enabled: bool,
    join_enabled: bool = True,
    enforce_readiness: bool = True,
    quiet_hours_enabled: bool = False,
    quiet_hours_start: int = 0,
    quiet_hours_end: int = 0,
    max_daily_actions: int = 0,
    gemini_api_key: str | None,
    gemini_model: str | None = None,
) -> WarmingSettingsSecret:
    """Persist warming settings.

    ``gemini_api_key=None`` leaves the stored key intact; empty string clears it.
    ``gemini_model=None`` or empty leaves the stored model intact.
    """
    return await asyncio.to_thread(
        _save_warming_settings,
        inter_account_chat=inter_account_chat,
        reactions_enabled=reactions_enabled,
        join_enabled=join_enabled,
        enforce_readiness=enforce_readiness,
        quiet_hours_enabled=quiet_hours_enabled,
        quiet_hours_start=quiet_hours_start,
        quiet_hours_end=quiet_hours_end,
        max_daily_actions=max_daily_actions,
        gemini_api_key=gemini_api_key,
        gemini_model=gemini_model,
    )


def _row_to_warming_state_record(mapping: Mapping[str, object]) -> WarmingStateRecord:
    return WarmingStateRecord(
        account_id=str(mapping["account_id"]),
        state=cast("WarmingState", mapping["state"]),
        cycles_completed=_required_int(mapping["cycles_completed"]),
        last_event=_optional_str(mapping.get("last_event")),
        last_cycle_at=_optional_str(mapping.get("last_cycle_at")),
        next_run_at=_optional_str(mapping.get("next_run_at")),
        updated_at=str(mapping["updated_at"]),
        last_error=_optional_str(mapping.get("last_error")),
        last_action=_optional_str(mapping.get("last_action")),
        last_channel=_optional_str(mapping.get("last_channel")),
        heartbeat_at=_optional_str(mapping.get("heartbeat_at")),
        started_at=_optional_str(mapping.get("started_at")),
        stopped_at=_optional_str(mapping.get("stopped_at")),
        flood_wait_seconds=_optional_int(mapping.get("flood_wait_seconds")),
        flood_wait_until=_optional_str(mapping.get("flood_wait_until")),
        proxy_snapshot=_optional_str(mapping.get("proxy_snapshot")),
        daily_actions=_optional_int(mapping.get("daily_actions")) or 0,
        daily_count_date=_optional_str(mapping.get("daily_count_date")),
        quarantine_count=_optional_int(mapping.get("quarantine_count")) or 0,
    )


def _list_warming_states() -> list[WarmingStateRecord]:
    statement = select(_warming_account_state)
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [_row_to_warming_state_record(cast("Mapping[str, object]", row)) for row in rows]


async def list_warming_states() -> list[WarmingStateRecord]:
    return await asyncio.to_thread(_list_warming_states)


def _fetch_warming_state(account_id: str) -> WarmingStateRecord | None:
    statement = select(_warming_account_state).where(
        _warming_account_state.c.account_id == account_id,
    )
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    if row is None:
        return None
    return _row_to_warming_state_record(cast("Mapping[str, object]", row))


async def fetch_warming_state(account_id: str) -> WarmingStateRecord | None:
    return await asyncio.to_thread(_fetch_warming_state, account_id)


def _upsert_warming_state(data: WarmingStateWrite) -> WarmingStateRecord:
    now = _now_iso()
    values: dict[str, object | None] = {
        "state": data.state,
        "cycles_completed": data.cycles_completed,
        "last_event": data.last_event,
        "last_cycle_at": data.last_cycle_at,
        "next_run_at": data.next_run_at,
        "updated_at": now,
        "last_error": data.last_error,
        "last_action": data.last_action,
        "last_channel": data.last_channel,
        "heartbeat_at": data.heartbeat_at,
        "started_at": data.started_at,
        "stopped_at": data.stopped_at,
        "flood_wait_seconds": data.flood_wait_seconds,
        "flood_wait_until": data.flood_wait_until,
        "proxy_snapshot": data.proxy_snapshot,
        "daily_actions": data.daily_actions,
        "daily_count_date": data.daily_count_date,
        "quarantine_count": data.quarantine_count,
    }
    with _get_engine().begin() as connection:
        exists = connection.execute(
            select(_warming_account_state.c.account_id).where(
                _warming_account_state.c.account_id == data.account_id,
            ),
        ).first()
        if exists is None:
            connection.execute(
                insert(_warming_account_state).values(account_id=data.account_id, **values),
            )
        else:
            connection.execute(
                update(_warming_account_state)
                .where(_warming_account_state.c.account_id == data.account_id)
                .values(**values),
            )
    record = _fetch_warming_state(data.account_id)
    if record is None:
        msg = f"Warming state was not persisted: {data.account_id}"
        raise RuntimeError(msg)
    return record


async def upsert_warming_state(data: WarmingStateWrite) -> WarmingStateRecord:
    return await asyncio.to_thread(_upsert_warming_state, data)


# --------------------------------------------------------------------------- #
# Domain repositories (#38) — split out of this module and re-exported so that
# existing ``from core.db import ...`` call sites keep working unchanged. These
# imports live at the bottom because the repositories import shared table
# objects and helpers defined above.
# --------------------------------------------------------------------------- #
from core.repositories.content import (  # noqa: E402, F401
    record_sent_hash,
    was_hash_sent_since,
)
from core.repositories.device_fingerprint import (  # noqa: E402, F401
    fetch_device_fingerprint,
    insert_device_fingerprint,
)
from core.repositories.dialogues import (  # noqa: E402, F401
    list_dialogue_pairs,
    replace_dialogue_pairs,
)
from core.repositories.spam_status import (  # noqa: E402, F401
    get_spam_status,
    upsert_spam_status,
)
