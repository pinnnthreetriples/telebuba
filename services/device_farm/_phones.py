"""The device farm's phones: list them with their status, start and stop them.

A phone is one mobile account's pod, named after it (``container_name``), or an
independent emulator bound to no account (``_standalone``, ``standalone_name``); its
id is that container name, so it stays the same before the container exists and
after. Starting an account's phone only boots it for the operator
(``boot_account_phone``): its Telegram is neither installed nor signed in, whatever the
account's main session can do; an independent emulator's start is its own
(``_standalone``). A start runs in the background because a boot takes minutes; the
list shows ``starting`` meanwhile.
Stopping refuses a phone that a start or an activity session holds: the warming
board owns those.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_device_fingerprint, list_accounts, list_proxies
from core.device_fingerprint_android import android_device_for
from core.emulator import EmulatorError, list_instances
from core.logging import log_event
from core.repositories.standalone_emulators import list_standalone_emulators
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
    boot_account_phone,
    container_name,
    phone_activity,
    stop_account_pod,
)
from services.device_farm import _standalone

if TYPE_CHECKING:
    from core.emulator import Instance
    from schemas.accounts import AccountRead
    from schemas.device_farm import PhoneStatus
    from schemas.device_fingerprint import DeviceFingerprint
    from schemas.emulator import EmulatorRefusalCode
    from schemas.proxy import ProxyRead
    from schemas.standalone_emulator import StandaloneEmulator, StandaloneEmulatorCreate

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


def _names(device_model: str) -> tuple[str, str]:
    """The maker and the sales name of a catalogue phone; the raw model otherwise."""
    device = android_device_for(device_model)
    if device is None:
        return "", device_model
    return device.manufacturer[:1].upper() + device.manufacturer[1:], device.name


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
    maker, model_name = _names(fingerprint.device_model)
    return Phone(
        phone_id=container_name(account.account_id),
        label=account.label,
        created=container is not None,
        status=_status(account.account_id, container),
        maker=maker,
        model_name=model_name,
        device_model=fingerprint.device_model,
        system_version=fingerprint.system_version,
        app_version=fingerprint.app_version,
        lang_code=fingerprint.system_lang_code,
        proxy=proxy,
        proxy_id=account.proxy_id,
        apps=[
            PhoneApp(
                platform="telegram",
                account_id=account.account_id,
                label=account.label,
                username=account.username,
            ),
        ],
    )


def _phone_proxy(proxy: ProxyRead | None) -> PhoneProxy | None:
    if proxy is None:
        return None
    return PhoneProxy(
        proxy_type=proxy.proxy_type,
        host=proxy.host,
        port=proxy.port,
        country_code=proxy.country_code,
        status=proxy.status,
    )


def _standalone_phone(
    emulator: StandaloneEmulator, proxy: ProxyRead | None, container: Instance | None
) -> Phone:
    fingerprint = emulator.fingerprint
    running = container is not None and container.running
    maker, model_name = _names(fingerprint.device_model)
    return Phone(
        phone_id=_standalone.standalone_name(emulator.id),
        kind="standalone",
        label=emulator.label,
        created=container is not None,
        status=_standalone.standalone_activity(emulator.id) or ("free" if running else "asleep"),
        maker=maker,
        model_name=model_name,
        device_model=fingerprint.device_model,
        system_version=fingerprint.system_version,
        app_version=fingerprint.app_version,
        lang_code=fingerprint.system_lang_code,
        proxy=_phone_proxy(proxy),
        proxy_id=emulator.proxy_id,
        apps=[],
        install_apps=emulator.install_apps,
    )


async def _pool_proxy(proxy_id: str | None, pool: dict[str, ProxyRead] | None) -> ProxyRead | None:
    if proxy_id is None:
        return None
    if pool is None:
        pool = {p.id: p for p in (await list_proxies()).proxies}
    return pool.get(proxy_id)


async def list_phones() -> PhoneList:
    """Every mobile account's phone, then every independent one, and why none can start."""
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
    standalone = await list_standalone_emulators()
    if standalone:
        pool = {p.id: p for p in (await list_proxies()).proxies}
        phones += [
            _standalone_phone(
                emulator,
                await _pool_proxy(emulator.proxy_id, pool),
                containers.get(_standalone.standalone_name(emulator.id)),
            )
            for emulator in standalone
        ]
    return PhoneList(enabled=problem is None, problem=problem, phones=phones)


async def create_standalone_phone(data: StandaloneEmulatorCreate) -> Phone:
    """A new independent emulator, as the farm lists it (asleep, no container yet)."""
    emulator = await _standalone.create_standalone(data)
    return _standalone_phone(emulator, await _pool_proxy(emulator.proxy_id, None), None)


def _standalone_id(phone_id: str) -> str:
    emulator_id = _standalone.standalone_of(phone_id)
    if emulator_id is None:
        raise EmulatorRefusedError(_UNKNOWN_PHONE)
    return emulator_id


async def delete_standalone_phone(phone_id: str) -> None:
    """Remove an independent emulator, its pod and its ``/data``."""
    await _standalone.delete_standalone(_standalone_id(phone_id))


async def set_standalone_phone_proxy(phone_id: str, proxy_id: str | None) -> None:
    """Put an independent emulator on ``proxy_id`` (``None``: on none); stops its pod."""
    await _standalone.set_standalone_phone_proxy(_standalone_id(phone_id), proxy_id)


async def _start_in_background(account_id: str) -> None:
    try:
        await boot_account_phone(account_id)
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
    """Boot the phone in the background, its app untouched; ``starting`` at once."""
    emulator_id = _standalone.standalone_of(phone_id)
    if emulator_id is not None:
        await _standalone.start_standalone(emulator_id)
        return PhoneActionResult(phone_id=phone_id, status="starting")
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
    emulator_id = _standalone.standalone_of(phone_id)
    if emulator_id is not None:
        await _standalone.stop_standalone(emulator_id)
        return PhoneActionResult(phone_id=phone_id, status="asleep")
    account_id = account_of(phone_id)
    await require_account(account_id)
    if phone_activity(account_id) in {"starting", "warming"}:
        raise EmulatorBusyError
    await stop_account_pod(account_id)
    return PhoneActionResult(phone_id=phone_id, status="asleep")
