"""Independent emulators' persistence: phones bound to no account, each on one pool proxy.

A proxy's capacity counts them alongside its accounts (``proxies.count_for_proxy``),
so a slot is claimed under ``BEGIN IMMEDIATE`` the same way an account's assignment is.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from sqlalchemy import delete, insert, select, update

from core._schema_standalone_emulators import _standalone_emulators
from core.config import settings
from core.db import _get_engine, _now_iso, _optional_str, _proxies
from core.repositories.proxies import ProxyCapacityError, count_for_proxy
from schemas.device_fingerprint import DeviceFingerprint
from schemas.standalone_emulator import StandaloneApp, StandaloneEmulator

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.engine import Connection


def _apps_of(stored: object) -> list[StandaloneApp]:
    """The comma-joined column as names; one this code no longer knows is dropped."""
    known: tuple[StandaloneApp, ...] = ("telegram", "gosuslugi")
    names = str(stored or "").split(",")
    return [app for app in known if app in names]


def _row_to_emulator(row: Mapping[str, object]) -> StandaloneEmulator:
    emulator_id = str(row["id"])
    return StandaloneEmulator(
        id=emulator_id,
        label=_optional_str(row.get("label")),
        proxy_id=_optional_str(row.get("proxy_id")),
        install_apps=_apps_of(row.get("install_apps")),
        fingerprint=DeviceFingerprint(
            account_id=emulator_id,
            device_type="mobile",
            platform="android",
            device_model=str(row["device_model"]),
            system_version=str(row["system_version"]),
            app_version=str(row["app_version"]),
            lang_code=str(row["lang_code"]),
            system_lang_code=str(row["system_lang_code"]),
        ),
        created_at=str(row["created_at"]),
    )


def _claim_slot(connection: Connection, proxy_id: str, current: str | None) -> None:
    """Refuse an unknown proxy (``ValueError``) or a full one (``ProxyCapacityError``)."""
    found = connection.execute(select(_proxies.c.id).where(_proxies.c.id == proxy_id)).first()
    if found is None:
        msg = f"Proxy not found: {proxy_id}"
        raise ValueError(msg)
    capacity = settings.proxy.max_accounts_per_proxy
    if current != proxy_id and count_for_proxy(connection, proxy_id) >= capacity:
        msg = f"Proxy {proxy_id} is at capacity ({capacity})"
        raise ProxyCapacityError(msg)


def _create(
    emulator_id: str,
    label: str | None,
    proxy_id: str | None,
    fingerprint: DeviceFingerprint,
    install_apps: list[StandaloneApp],
) -> StandaloneEmulator:
    apps = _apps_of(",".join(install_apps))
    emulator = StandaloneEmulator(
        id=emulator_id,
        label=label,
        proxy_id=proxy_id,
        install_apps=apps,
        fingerprint=fingerprint.model_copy(update={"account_id": emulator_id}),
        created_at=_now_iso(),
    )
    with _get_engine().begin() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        if proxy_id is not None:
            _claim_slot(connection, proxy_id, None)
        connection.execute(
            insert(_standalone_emulators).values(
                id=emulator_id,
                label=label,
                proxy_id=proxy_id,
                install_apps=",".join(apps),
                device_model=fingerprint.device_model,
                system_version=fingerprint.system_version,
                app_version=fingerprint.app_version,
                lang_code=fingerprint.lang_code,
                system_lang_code=fingerprint.system_lang_code,
                created_at=emulator.created_at,
            ),
        )
    return emulator


async def create_standalone_emulator(
    emulator_id: str,
    label: str | None,
    proxy_id: str | None,
    fingerprint: DeviceFingerprint,
    install_apps: list[StandaloneApp],
) -> StandaloneEmulator:
    """Store a new emulator, claiming its proxy's slot when it has one."""
    return await asyncio.to_thread(_create, emulator_id, label, proxy_id, fingerprint, install_apps)


def _list() -> list[StandaloneEmulator]:
    statement = select(_standalone_emulators).order_by(_standalone_emulators.c.created_at)
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [_row_to_emulator(cast("Mapping[str, object]", row)) for row in rows]


async def list_standalone_emulators() -> list[StandaloneEmulator]:
    return await asyncio.to_thread(_list)


def _fetch(emulator_id: str) -> StandaloneEmulator | None:
    statement = select(_standalone_emulators).where(_standalone_emulators.c.id == emulator_id)
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    return None if row is None else _row_to_emulator(cast("Mapping[str, object]", row))


async def fetch_standalone_emulator(emulator_id: str) -> StandaloneEmulator | None:
    return await asyncio.to_thread(_fetch, emulator_id)


def _set_proxy(emulator_id: str, proxy_id: str | None) -> bool:
    table = _standalone_emulators
    with _get_engine().begin() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        row = connection.execute(select(table.c.proxy_id).where(table.c.id == emulator_id)).first()
        if row is None:
            return False
        if proxy_id is not None:
            _claim_slot(connection, proxy_id, _optional_str(row[0]))
        connection.execute(update(table).where(table.c.id == emulator_id).values(proxy_id=proxy_id))
    return True


async def set_standalone_proxy(emulator_id: str, proxy_id: str | None) -> bool:
    """Put the emulator on ``proxy_id`` (``None``: on none); ``False`` for an unknown one."""
    return await asyncio.to_thread(_set_proxy, emulator_id, proxy_id)


def _delete(emulator_id: str) -> bool:
    table = _standalone_emulators
    with _get_engine().begin() as connection:
        result = connection.execute(delete(table).where(table.c.id == emulator_id))
    return result.rowcount > 0


async def delete_standalone_emulator(emulator_id: str) -> bool:
    return await asyncio.to_thread(_delete, emulator_id)


def _ids_for_proxy(proxy_id: str) -> list[str]:
    table = _standalone_emulators
    statement = select(table.c.id).where(table.c.proxy_id == proxy_id)
    with _get_engine().connect() as connection:
        return [str(row[0]) for row in connection.execute(statement).all()]


async def list_standalone_ids_for_proxy(proxy_id: str) -> list[str]:
    """The emulators on ``proxy_id`` (their pods stop when it goes)."""
    return await asyncio.to_thread(_ids_for_proxy, proxy_id)
