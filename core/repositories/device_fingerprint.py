"""Device-fingerprint repository (split out of core.db for #38).

Owns the inserts and the reads of the ``device_fingerprints`` table. Shared
plumbing is imported from ``core.db``; the public async functions are
re-exported by ``core.db`` so callers are unaffected.

One row per authorization, keyed ``(account_id, device_type)``. Reads that want
"the account's fingerprint" get the PRIMARY device's row (desktop when the account
has one) through ``_primary_device``, so a second authorization changes nothing a
one-fingerprint consumer sees. Rows are only ever inserted: there is no upsert, and
the table's one UPDATE — the language correction — is not here: it belongs to the
session check that learns the phone, and lives in
``core.repositories._device_fingerprint_language`` (see that module's docstring
for why it cannot be imported from here).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from core.db import _accounts, _device_fingerprints, _get_engine, _row_to_device_fingerprint
from core.repositories._primary_device import joins_primary_fingerprint
from schemas.device_fingerprint import other_device

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.sql import Select

    from schemas.device_fingerprint import DeviceFingerprint, DeviceType


class SecondDeviceConflictError(ValueError):
    """The account already has a second device, or no longer exists."""


def _primary_fingerprints() -> Select:
    return select(_device_fingerprints).select_from(
        _accounts.join(_device_fingerprints, joins_primary_fingerprint()),
    )


def _fetch_device_fingerprint(
    account_id: str,
    device_type: DeviceType | None,
) -> DeviceFingerprint | None:
    if device_type is None:
        statement = _primary_fingerprints().where(_accounts.c.account_id == account_id)
    else:
        statement = select(_device_fingerprints).where(
            _device_fingerprints.c.account_id == account_id,
            _device_fingerprints.c.device_type == device_type,
        )
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    if row is None:
        return None
    return _row_to_device_fingerprint(cast("Mapping[str, object]", row))


async def fetch_device_fingerprint(
    account_id: str,
    device_type: DeviceType | None = None,
) -> DeviceFingerprint | None:
    """One device's fingerprint; ``device_type=None`` reads the primary device's."""
    return await asyncio.to_thread(_fetch_device_fingerprint, account_id, device_type)


def _list_device_fingerprints() -> dict[str, DeviceFingerprint]:
    with _get_engine().connect() as connection:
        rows = connection.execute(_primary_fingerprints()).mappings().all()
    return {
        str(row["account_id"]): _row_to_device_fingerprint(cast("Mapping[str, object]", row))
        for row in rows
    }


async def list_device_fingerprints() -> dict[str, DeviceFingerprint]:
    """Every account's primary-device fingerprint keyed by ``account_id`` (one query)."""
    return await asyncio.to_thread(_list_device_fingerprints)


def _list_device_fingerprints_by_ids(account_ids: list[str]) -> dict[str, DeviceFingerprint]:
    if not account_ids:
        return {}
    statement = _primary_fingerprints().where(_accounts.c.account_id.in_(account_ids))
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return {
        str(row["account_id"]): _row_to_device_fingerprint(cast("Mapping[str, object]", row))
        for row in rows
    }


async def list_device_fingerprints_by_ids(account_ids: list[str]) -> dict[str, DeviceFingerprint]:
    """Primary-device fingerprints for a specific set of accounts keyed by ``account_id``."""
    return await asyncio.to_thread(_list_device_fingerprints_by_ids, account_ids)


def _insert_device_fingerprint(profile: DeviceFingerprint) -> DeviceFingerprint:
    statement = insert(_device_fingerprints).values(**profile.model_dump())
    with _get_engine().begin() as connection:
        connection.execute(statement)
    return profile


async def insert_device_fingerprint(profile: DeviceFingerprint) -> DeviceFingerprint:
    """Insert ``profile``, or return the row a concurrent insert already wrote — never both."""
    try:
        return await asyncio.to_thread(_insert_device_fingerprint, profile)
    except IntegrityError:
        existing = await fetch_device_fingerprint(profile.account_id, profile.device_type)
        if existing is None:
            raise
        return existing


def _record_second_device(second_session_name: str, profile: DeviceFingerprint) -> None:
    with _get_engine().begin() as connection:
        claimed = connection.execute(
            update(_accounts)
            .where(
                _accounts.c.account_id == profile.account_id,
                _accounts.c.second_session_name.is_(None),
                _accounts.c.session_device != profile.device_type,
            )
            .values(second_session_name=second_session_name),
        )
        if claimed.rowcount != 1:
            msg = f"account {profile.account_id!r} cannot take a second {profile.device_type}"
            raise SecondDeviceConflictError(msg)
        connection.execute(insert(_device_fingerprints).values(**profile.model_dump()))


async def record_second_device(second_session_name: str, profile: DeviceFingerprint) -> None:
    """Register a signed-in second authorization: its session name AND its fingerprint.

    One transaction, so the account never lists a device whose fingerprint is missing,
    and the ``second_session_name IS NULL`` guard makes a second call refuse instead of
    replacing the first. An IntegrityError on the insert (a stale row of that device)
    rolls the session name back with it.
    """
    await asyncio.to_thread(_record_second_device, second_session_name, profile)


def _forget_second_device(account_id: str) -> None:
    with _get_engine().begin() as connection:
        session_device = connection.execute(
            select(_accounts.c.session_device).where(_accounts.c.account_id == account_id),
        ).scalar_one_or_none()
        if session_device is None:
            return
        connection.execute(
            update(_accounts)
            .where(_accounts.c.account_id == account_id)
            .values(second_session_name=None),
        )
        connection.execute(
            delete(_device_fingerprints).where(
                _device_fingerprints.c.account_id == account_id,
                _device_fingerprints.c.device_type == other_device(session_device),
            ),
        )


async def forget_second_device(account_id: str) -> None:
    """Drop the account's second device: its session name and its fingerprint row.

    For a device whose sign-in has ended. Nothing is rewritten — the device is gone,
    and adding it again mints a fresh fingerprint for a fresh sign-in.
    """
    await asyncio.to_thread(_forget_second_device, account_id)
