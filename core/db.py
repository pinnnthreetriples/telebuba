from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from sqlalchemy import Column, MetaData, String, Table, create_engine, insert, select
from sqlalchemy.exc import IntegrityError

from core.config import settings
from schemas.device_fingerprint import DeviceFingerprint, DevicePlatform

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from sqlalchemy.engine import Engine


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
        database_path = _state.database_path or settings.database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        _state.engine = create_engine(
            f"sqlite:///{database_path}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        _metadata.create_all(_state.engine)
    return _state.engine


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
