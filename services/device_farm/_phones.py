"""The device farm's phones: list them with their status, start and stop them.

A phone is today one mobile account's pod, named after it (``container_name``); its
id is that container name, so it stays the same before the container exists and
after. Starting is the account's own open (boot, seed, sign the app in), run in the
background because it takes minutes; the list shows ``starting`` meanwhile.
Stopping refuses a phone that a start or an activity session holds: the warming
board owns those.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_device_fingerprint, list_accounts
from core.emulator import EmulatorError, list_instances
from core.logging import log_event
from schemas.device_farm import (
    Phone,
    PhoneActionResult,
    PhoneApp,
    PhoneList,
    PhoneProxy,
)
from services.accounts._result import AccountActionError
from services.accounts.lifecycle import require_account
from services.accounts.mobile_emulator import (
    EmulatorBusyError,
    EmulatorRefusedError,
    container_name,
    open_account_mobile,
    phone_activity,
    stop_account_pod,
)

if TYPE_CHECKING:
    from core.emulator import Instance
    from schemas.accounts import AccountRead
    from schemas.device_farm import PhoneStatus
    from schemas.device_fingerprint import DeviceFingerprint
    from schemas.emulator import EmulatorRefusalCode

_DISABLED: EmulatorRefusalCode = "mobile_emulator_disabled"
_UNKNOWN_PHONE: EmulatorRefusalCode = "emulator_not_found"

# Starts in flight, kept so the event loop does not drop them half way.
_STARTS: set[asyncio.Task[None]] = set()


def account_of(phone_id: str) -> str:
    """The account a phone id names; ``emulator_not_found`` for any other id."""
    account_id = phone_id.removeprefix(settings.emulator.instance_prefix)
    if account_id == phone_id or not account_id:
        raise EmulatorRefusedError(_UNKNOWN_PHONE)
    return account_id


async def _containers() -> tuple[dict[str, Instance], EmulatorRefusalCode | None]:
    """This app's Android containers by name, or why they cannot be listed."""
    if not settings.emulator.enabled:
        return {}, _DISABLED
    try:
        return {i.name: i for i in await list_instances()}, None
    except EmulatorError as exc:
        return {}, exc.code


def _status(account_id: str, container: Instance | None) -> PhoneStatus:
    activity = phone_activity(account_id)
    if activity is not None:
        return activity
    return "free" if container is not None and container.running else "asleep"


def _phone(
    account: AccountRead, fingerprint: DeviceFingerprint, container: Instance | None
) -> Phone:
    proxy = None
    if account.proxy_id is not None:
        proxy = PhoneProxy(
            proxy_type=account.proxy_type,
            host=account.proxy_host,
            port=account.proxy_port,
            country_code=account.proxy_country_code,
            status=account.proxy_status,
        )
    return Phone(
        phone_id=container_name(account.account_id),
        created=container is not None,
        status=_status(account.account_id, container),
        device_model=fingerprint.device_model,
        system_version=fingerprint.system_version,
        app_version=fingerprint.app_version,
        lang_code=fingerprint.system_lang_code,
        proxy=proxy,
        apps=[
            PhoneApp(
                platform="telegram",
                account_id=account.account_id,
                label=account.label,
                username=account.username,
            ),
        ],
    )


async def list_phones() -> PhoneList:
    """Every account with a mobile device, as a phone, plus why none can start (if so)."""
    mobile = [a for a in (await list_accounts()).accounts if "mobile" in a.devices]
    fingerprints = await asyncio.gather(
        *(fetch_device_fingerprint(a.account_id, "mobile") for a in mobile)
    )
    containers, problem = await _containers()
    phones = [
        _phone(account, fingerprint, containers.get(container_name(account.account_id)))
        for account, fingerprint in zip(mobile, fingerprints, strict=True)
        if fingerprint is not None
    ]
    return PhoneList(enabled=problem is None, problem=problem, phones=phones)


async def _start_in_background(account_id: str) -> None:
    try:
        await open_account_mobile(account_id)
    except AccountActionError:
        return  # logged by the open itself (``account_mobile_open_failed``)
    except Exception as exc:  # noqa: BLE001 - a background task has nobody to raise to
        await log_event(
            "WARNING",
            "account_mobile_open_failed",
            account_id=account_id,
            extra={"reason": type(exc).__name__},
        )


async def start_phone(phone_id: str) -> PhoneActionResult:
    """Start the phone and sign its app in, in the background; ``starting`` at once."""
    account_id = account_of(phone_id)
    await require_account(account_id)
    if not settings.emulator.enabled:
        raise EmulatorRefusedError(_DISABLED)
    if phone_activity(account_id) is not None:
        raise EmulatorBusyError
    task = asyncio.create_task(_start_in_background(account_id))
    _STARTS.add(task)
    task.add_done_callback(_STARTS.discard)
    await asyncio.sleep(0)  # let the open take the account's lock before answering
    return PhoneActionResult(phone_id=phone_id, status="starting")


async def stop_phone(phone_id: str) -> PhoneActionResult:
    """Put the phone to sleep (its ``/data`` stays); refused while it starts or warms."""
    account_id = account_of(phone_id)
    await require_account(account_id)
    if phone_activity(account_id) in {"starting", "warming"}:
        raise EmulatorBusyError
    await stop_account_pod(account_id)
    return PhoneActionResult(phone_id=phone_id, status="asleep")
