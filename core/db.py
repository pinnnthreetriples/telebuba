from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

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
    AccountProxyDelete,
    AccountProxyRead,
    AccountProxySettings,
    AccountProxyUpsert,
    ProxyStatus,
    ProxyType,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from sqlalchemy.engine import Engine
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
        _state.engine = create_engine(
            f"sqlite:///{database_path}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        _metadata.create_all(_state.engine)
        _ensure_sqlite_schema(_state.engine)
    return _state.engine


def _ensure_sqlite_schema(engine: Engine) -> None:
    """Tiny additive migration hook for local SQLite files created before new columns."""
    with engine.begin() as connection:
        rows = connection.exec_driver_sql("PRAGMA table_info(accounts)").mappings().all()
        existing = {str(row["name"]) for row in rows}
        if "bio" not in existing:
            connection.exec_driver_sql("ALTER TABLE accounts ADD COLUMN bio VARCHAR")


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


def _fetch_device_fingerprint(account_id: str) -> DeviceFingerprint | None:
    statement = select(_device_fingerprints).where(_device_fingerprints.c.account_id == account_id)
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    if row is None:
        return None
    return _row_to_device_fingerprint(cast("Mapping[str, object]", row))


async def fetch_device_fingerprint(account_id: str) -> DeviceFingerprint | None:
    return await asyncio.to_thread(_fetch_device_fingerprint, account_id)


def _insert_device_fingerprint(profile: DeviceFingerprint) -> DeviceFingerprint:
    statement = insert(_device_fingerprints).values(**profile.model_dump())
    with _get_engine().begin() as connection:
        connection.execute(statement)
    return profile


async def insert_device_fingerprint(profile: DeviceFingerprint) -> DeviceFingerprint:
    try:
        return await asyncio.to_thread(_insert_device_fingerprint, profile)
    except IntegrityError:
        existing = await fetch_device_fingerprint(profile.account_id)
        if existing is None:
            raise
        return existing


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
