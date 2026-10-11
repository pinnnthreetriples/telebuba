"""Device-fingerprint repository (split out of core.db for #38).

Owns the insert and the reads of the ``device_fingerprints`` table. Shared
plumbing is imported from ``core.db``; the public async functions are
re-exported by ``core.db`` so callers are unaffected.

The table's one UPDATE — the language correction — is not here: it belongs to the
session check that learns the phone, and lives in
``core.repositories._device_fingerprint_language`` (see that module's docstring
for why it cannot be imported from here).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from sqlalchemy import insert, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from core.db import _device_fingerprints, _get_engine, _row_to_device_fingerprint

if TYPE_CHECKING:
    from collections.abc import Mapping

    from schemas.device_fingerprint import DeviceFingerprint, DeviceType


def _fetch_device_fingerprint(
    account_id: str,
    device_type: DeviceType = "desktop",
) -> DeviceFingerprint | None:
    statement = select(_device_fingerprints).where(
        (_device_fingerprints.c.account_id == account_id)
        & (_device_fingerprints.c.device_type == device_type),
    )
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    if row is None:
        return None
    return _row_to_device_fingerprint(cast("Mapping[str, object]", row))


async def fetch_device_fingerprint(
    account_id: str,
    *,
    device_type: DeviceType = "desktop",
) -> DeviceFingerprint | None:
    return await asyncio.to_thread(_fetch_device_fingerprint, account_id, device_type)


get_device_fingerprint = fetch_device_fingerprint


def _list_account_device_fingerprints(account_id: str) -> list[DeviceFingerprint]:
    statement = (
        select(_device_fingerprints)
        .where(_device_fingerprints.c.account_id == account_id)
        .order_by(_device_fingerprints.c.device_type.asc())
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [_row_to_device_fingerprint(cast("Mapping[str, object]", row)) for row in rows]


async def list_account_device_fingerprints(account_id: str) -> list[DeviceFingerprint]:
    """Return all device fingerprints (desktop and/or mobile) for ``account_id``."""
    return await asyncio.to_thread(_list_account_device_fingerprints, account_id)


def _list_device_fingerprints() -> dict[str, DeviceFingerprint]:
    statement = select(_device_fingerprints).order_by(_device_fingerprints.c.device_type.desc())
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return {
        str(row["account_id"]): _row_to_device_fingerprint(cast("Mapping[str, object]", row))
        for row in rows
    }


async def list_device_fingerprints() -> dict[str, DeviceFingerprint]:
    """Return every device fingerprint keyed by ``account_id`` (one query)."""
    return await asyncio.to_thread(_list_device_fingerprints)


def _list_device_fingerprints_by_ids(account_ids: list[str]) -> dict[str, DeviceFingerprint]:
    if not account_ids:
        return {}
    statement = (
        select(_device_fingerprints)
        .where(_device_fingerprints.c.account_id.in_(account_ids))
        .order_by(_device_fingerprints.c.device_type.desc())
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return {
        str(row["account_id"]): _row_to_device_fingerprint(cast("Mapping[str, object]", row))
        for row in rows
    }


async def list_device_fingerprints_by_ids(account_ids: list[str]) -> dict[str, DeviceFingerprint]:
    """Device fingerprints for a specific set of accounts keyed by ``account_id``."""
    return await asyncio.to_thread(_list_device_fingerprints_by_ids, account_ids)


def _insert_device_fingerprint(profile: DeviceFingerprint) -> DeviceFingerprint:
    statement = insert(_device_fingerprints).values(**profile.model_dump())
    with _get_engine().begin() as connection:
        connection.execute(statement)
    return profile


async def insert_device_fingerprint(profile: DeviceFingerprint) -> DeviceFingerprint:
    try:
        return await asyncio.to_thread(_insert_device_fingerprint, profile)
    except IntegrityError:
        existing = await fetch_device_fingerprint(
            profile.account_id,
            device_type=profile.device_type,
        )
        if existing is None:
            raise
        return existing


def _upsert_device_fingerprint(profile: DeviceFingerprint) -> DeviceFingerprint:
    values = profile.model_dump()
    stmt = sqlite_insert(_device_fingerprints).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["account_id", "device_type"],
        set_={
            "platform": stmt.excluded.platform,
            "device_model": stmt.excluded.device_model,
            "system_version": stmt.excluded.system_version,
            "app_version": stmt.excluded.app_version,
            "lang_code": stmt.excluded.lang_code,
            "system_lang_code": stmt.excluded.system_lang_code,
        },
    )
    with _get_engine().begin() as connection:
        connection.execute(stmt)
    return profile


async def upsert_device_fingerprint(profile: DeviceFingerprint) -> DeviceFingerprint:
    """Insert or update a ``(account_id, device_type)`` fingerprint row."""
    return await asyncio.to_thread(_upsert_device_fingerprint, profile)
