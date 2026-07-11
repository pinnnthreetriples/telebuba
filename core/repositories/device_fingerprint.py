"""Device-fingerprint repository (split out of core.db for #38).

Owns reads/writes of the ``device_fingerprints`` table. Shared plumbing is
imported from ``core.db``; the public async functions are re-exported by
``core.db`` so callers are unaffected.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from core.db import _device_fingerprints, _get_engine, _row_to_device_fingerprint

if TYPE_CHECKING:
    from collections.abc import Mapping

    from schemas.device_fingerprint import DeviceFingerprint


def _fetch_device_fingerprint(account_id: str) -> DeviceFingerprint | None:
    statement = select(_device_fingerprints).where(_device_fingerprints.c.account_id == account_id)
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    if row is None:
        return None
    return _row_to_device_fingerprint(cast("Mapping[str, object]", row))


async def fetch_device_fingerprint(account_id: str) -> DeviceFingerprint | None:
    return await asyncio.to_thread(_fetch_device_fingerprint, account_id)


def _list_device_fingerprints() -> dict[str, DeviceFingerprint]:
    with _get_engine().connect() as connection:
        rows = connection.execute(select(_device_fingerprints)).mappings().all()
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
    statement = select(_device_fingerprints).where(
        _device_fingerprints.c.account_id.in_(account_ids),
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
        existing = await fetch_device_fingerprint(profile.account_id)
        if existing is None:
            raise
        return existing
