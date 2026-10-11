"""Zero-Click native Android emulator (LDPlayer 9 + ADB) launcher and login orchestrator."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

from core.db import (
    fetch_account,
    fetch_account_proxy_settings,
    fetch_account_twofa_password,
)
from core.emulator import (
    KEYCODE_ENTER,
    EmulatorError,
    EmulatorNotFoundError,
    LoginCodeTimeoutError,
    configure_http_proxy,
    create_instance,
    deterministic_hardware_ids,
    input_text,
    is_running,
    launch_instance,
    press_key,
    set_device_profile,
    set_resolution,
    set_resource_limits,
    start_app,
    stop_instance,
    wait_for_boot,
    wait_for_telegram_login_code,
)
from core.web_login import LocalProxyRelay
from schemas.mobile_emulator import OpenMobileResult
from services.accounts._result import AccountNotFoundError

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.proxy import ProxySettings

__all__ = [
    "MobileEmulatorLaunchError",
    "MobileEmulatorNotFoundError",
    "MobileEmulatorServiceError",
    "MobileLoginCodeTimeoutError",
    "NoProxyForMobileError",
    "OpenMobileResult",
    "open_account_mobile",
    "shutdown_mobile_emulator",
]

_NO_PROXY_REFUSAL = "mobile_emulator_no_proxy"
_NO_PROFILE_REFUSAL = "mobile_emulator_no_profile"
_NOT_FOUND_REFUSAL = "mobile_emulator_not_found"
_CODE_TIMEOUT_REFUSAL = "mobile_emulator_code_timeout"
_LAUNCH_FAILED_REFUSAL = "mobile_emulator_launch_failed"
_SHUTDOWN_REFUSAL = "mobile_emulator_shutting_down"

_EMULATOR_HOST_LOOPBACK = "10.0.2.2"
_DEFAULT_SERIAL = "emulator-5554"


class MobileEmulatorServiceError(ValueError):
    """Base for operator-facing mobile emulator refusals (maps to HTTP 400)."""


class NoProxyForMobileError(MobileEmulatorServiceError):
    """Account has no proxy assigned; mobile emulator refuses direct host egress."""


class MobileEmulatorNotFoundError(MobileEmulatorServiceError):
    """LDPlayer (`ldconsole.exe`) or `adb.exe` binary was not found on the host."""


class MobileLoginCodeTimeoutError(MobileEmulatorServiceError):
    """Telethon did not receive the 5-digit login code from `777000` in time."""


class MobileEmulatorLaunchError(MobileEmulatorServiceError):
    """Failed to configure, boot, or drive the mobile emulator."""


_emulator_locks: dict[str, asyncio.Lock] = {}
_emulator_locks_guard = asyncio.Lock()
_relays: dict[str, tuple[LocalProxyRelay, ProxySettings]] = {}
_relays_lock = asyncio.Lock()
_active_instances: dict[str, str] = {}
_signed_in_accounts: set[str] = set()
_closing = asyncio.Event()


async def _account_emulator_lock(account_id: str) -> asyncio.Lock:
    async with _emulator_locks_guard:
        lock = _emulator_locks.get(account_id)
        if lock is None:
            lock = asyncio.Lock()
            _emulator_locks[account_id] = lock
        return lock


async def _relay_port_for(account_id: str, proxy: ProxySettings) -> int:
    async with _relays_lock:
        existing = _relays.get(account_id)
        if existing is not None and existing[1] == proxy and existing[0].port is not None:
            return existing[0].port
        old_relay = existing[0] if existing is not None else None
        _relays.pop(account_id, None)
    if old_relay is not None:
        with suppress(Exception):
            await old_relay.aclose()
    relay = LocalProxyRelay(proxy)
    try:
        port = await relay.start()
    except OSError as exc:
        raise MobileEmulatorLaunchError(_LAUNCH_FAILED_REFUSAL) from exc
    async with _relays_lock:
        if _closing.is_set():
            with suppress(Exception):
                await relay.aclose()
            raise MobileEmulatorLaunchError(_SHUTDOWN_REFUSAL)
        _relays[account_id] = (relay, proxy)
    return port


async def _provision_and_login_emulator(
    account: AccountRead,
    instance_name: str,
    serial: str,
    proxy: ProxySettings,
    *,
    running: bool,
) -> OpenMobileResult:
    account_id = account.account_id
    relay_port = await _relay_port_for(account_id, proxy)
    imei, android_id, mac = deterministic_hardware_ids(account_id)
    device_model = (
        account.mobile_fingerprint.device_model
        if account.mobile_fingerprint is not None
        else "Samsung Galaxy S24 Ultra"
    )

    if not running:
        await create_instance(instance_name)
        await set_device_profile(
            instance_name,
            model=device_model,
            imei=imei,
            android_id=android_id,
            mac=mac,
        )
        await set_resolution(instance_name, width=412, height=915, dpi=320)
        await set_resource_limits(instance_name, cpus=1, ram_mb=1536)
        await launch_instance(instance_name)
        _active_instances[account_id] = instance_name

    await wait_for_boot(serial)
    await configure_http_proxy(serial, _EMULATOR_HOST_LOOPBACK, relay_port)
    await start_app(serial)

    if account.phone:
        await input_text(serial, account.phone)
        await press_key(serial, KEYCODE_ENTER)

    code = await wait_for_telegram_login_code(account_id)
    await input_text(serial, code)
    await press_key(serial, KEYCODE_ENTER)

    twofa_password = await fetch_account_twofa_password(account_id)
    if twofa_password:
        await input_text(serial, twofa_password)
        await press_key(serial, KEYCODE_ENTER)

    _signed_in_accounts.add(account_id)
    return OpenMobileResult(
        launched=True,
        signed_in=True,
        instance_name=instance_name,
    )


async def open_account_mobile(account_id: str) -> OpenMobileResult:
    """Launch (or focus) the account's LDPlayer 9 instance and run Zero-Click login."""
    account = await fetch_account(account_id)
    if account is None:
        raise AccountNotFoundError(account_id)
    if not account.has_mobile_profile or account.mobile_fingerprint is None:
        raise MobileEmulatorLaunchError(_NO_PROFILE_REFUSAL)
    proxy = await fetch_account_proxy_settings(account_id)
    if proxy is None:
        raise NoProxyForMobileError(_NO_PROXY_REFUSAL)
    if _closing.is_set():
        raise MobileEmulatorLaunchError(_SHUTDOWN_REFUSAL)

    instance_name = f"telebuba_{account_id}"
    serial = _DEFAULT_SERIAL

    async with await _account_emulator_lock(account_id):
        try:
            running = await is_running(instance_name)
            if running and account_id in _signed_in_accounts:
                await start_app(serial)
                return OpenMobileResult(
                    launched=True,
                    signed_in=True,
                    instance_name=instance_name,
                )

            return await _provision_and_login_emulator(
                account,
                instance_name,
                serial,
                proxy,
                running=running,
            )
        except EmulatorNotFoundError as exc:
            raise MobileEmulatorNotFoundError(_NOT_FOUND_REFUSAL) from exc
        except LoginCodeTimeoutError as exc:
            raise MobileLoginCodeTimeoutError(_CODE_TIMEOUT_REFUSAL) from exc
        except (EmulatorError, OSError) as exc:
            raise MobileEmulatorLaunchError(_LAUNCH_FAILED_REFUSAL) from exc


async def shutdown_mobile_emulator() -> None:
    """Stop all running mobile emulator instances and close local proxy relays."""
    _closing.set()
    instances = list(_active_instances.values())
    _active_instances.clear()
    _signed_in_accounts.clear()
    for name in instances:
        with suppress(Exception):
            await stop_instance(name)

    async with _relays_lock:
        relays = [entry[0] for entry in _relays.values()]
        _relays.clear()
    for relay in relays:
        with suppress(Exception):
            await relay.aclose()
    _closing.clear()


def _reset_mobile_emulator_for_tests() -> None:
    """Reset in-memory state between unit tests."""
    _closing.clear()
    _emulator_locks.clear()
    _relays.clear()
    _active_instances.clear()
    _signed_in_accounts.clear()
