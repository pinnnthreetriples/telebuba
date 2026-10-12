"""Open a mobile account in its local Android container (LDPlayer 9).

The container is the account's MOBILE device made visible: it is created with the
mobile fingerprint's manufacturer and model, refused when its Android SDK differs,
and its Telegram app connects straight to the account's SOCKS5 proxy. The I/O lives
in ``core.emulator``; the policy lives here:

* refuse what cannot work before any container is touched — the feature switched
  off, no mobile device, no proxy or a proxy the app cannot use;
* one start at a time per account (a second one answers ``emulator_busy``);
* a NEW container starts with its network off and gets it back only once the app's
  proxy is set, so its first connection already goes through the proxy; the setting
  is the app's own and outlives the container's window being closed;
* the sign-in state is read off the screen every time, never remembered;
* on any failure the container this call started is stopped again, and removed when
  this call created it, so the next attempt redoes the network-off first start.

The per-account locks are process-local, like the rest of the runtime state (one
uvicorn worker), and dropped once their start is over.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_device_fingerprint
from core.emulator import (
    EmulatorError,
    adb_serial,
    apply_account_proxy,
    assert_sdk,
    container_spec,
    create_instance,
    ensure_app,
    find_instance,
    launch_instance,
    quit_instance,
    read_screen,
    remove_container,
    set_network,
    wait_for_boot,
)
from core.logging import log_event
from schemas.emulator import OpenMobileResult
from services.accounts._emulator_sign_in import sign_in
from services.accounts._result import AccountActionError
from services.accounts.devices import DeviceRefusedError
from services.accounts.lifecycle import require_account

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.device_fingerprint import DeviceFingerprint
    from schemas.devices import DeviceRefusalCode
    from schemas.emulator import EmulatorRefusalCode

_DEVICE_MISSING: DeviceRefusalCode = "device_missing"
_EMULATOR_PROXY_MISSING: EmulatorRefusalCode = "emulator_proxy_missing"
_EMULATOR_PROXY_UNSUPPORTED: EmulatorRefusalCode = "emulator_proxy_unsupported"
_MOBILE_EMULATOR_DISABLED: EmulatorRefusalCode = "mobile_emulator_disabled"

__all__ = [
    "EmulatorBusyError",
    "EmulatorRefusedError",
    "open_account_mobile",
    "remove_account_container",
]

# Stdlib sink, codes and step names only (see ``core.emulator._errors``).
logger = logging.getLogger(__name__)

_LOCKS: dict[str, asyncio.Lock] = {}


class EmulatorRefusedError(AccountActionError):
    """The container could not be opened; ``code`` is the stable code the SPA translates."""

    def __init__(self, code: EmulatorRefusalCode) -> None:
        super().__init__(code)


class EmulatorBusyError(EmulatorRefusedError):
    """Another start of the same account's container is still running (409)."""

    def __init__(self) -> None:
        super().__init__("emulator_busy")


def _container_name(account_id: str) -> str:
    return f"{settings.emulator.instance_prefix}{account_id}"


async def _refuse_what_cannot_work(account: AccountRead) -> DeviceFingerprint:
    """The mobile fingerprint to build the container from, or the reason there is none."""
    if not settings.emulator.enabled:
        raise EmulatorRefusedError(_MOBILE_EMULATOR_DISABLED)
    if "mobile" not in account.devices:
        raise DeviceRefusedError(_DEVICE_MISSING)
    if account.proxy_id is None:
        raise EmulatorRefusedError(_EMULATOR_PROXY_MISSING)
    if account.proxy_type != "socks5":
        raise EmulatorRefusedError(_EMULATOR_PROXY_UNSUPPORTED)
    fingerprint = await fetch_device_fingerprint(account.account_id, "mobile")
    if fingerprint is None:
        raise DeviceRefusedError(_DEVICE_MISSING)
    return fingerprint


async def open_account_mobile(account_id: str) -> OpenMobileResult:
    """Start (or find running) the account's container and sign its Telegram app in."""
    account = await require_account(account_id)
    fingerprint = await _refuse_what_cannot_work(account)
    lock = _LOCKS.setdefault(account_id, asyncio.Lock())
    if lock.locked():
        raise EmulatorBusyError
    try:
        async with lock:
            result = await _open(account, fingerprint)
    except EmulatorError as exc:
        await log_event(
            "WARNING",
            "account_mobile_open_failed",
            account_id=account_id,
            extra={"reason": exc.code},
        )
        raise EmulatorRefusedError(exc.code) from exc
    finally:
        if not lock.locked() and _LOCKS.get(account_id) is lock:
            del _LOCKS[account_id]
    await log_event("INFO", "account_mobile_opened", account_id=account_id)
    return result


async def _open(account: AccountRead, fingerprint: DeviceFingerprint) -> OpenMobileResult:
    spec = container_spec(fingerprint)
    name = _container_name(account.account_id)
    instance = await find_instance(name)
    created = instance is None
    if instance is None:
        instance = await create_instance(name, spec)
    serial = adb_serial(instance.index)
    if instance.running:
        screen, _nodes = await read_screen(serial)
        if screen == "chats":
            return OpenMobileResult(
                account_id=account.account_id, status="already_running", signed_in=True
            )
    launched = not instance.running
    try:
        if launched:
            await launch_instance(instance.index)
            await wait_for_boot(serial)
        await assert_sdk(serial, spec.sdk)
        if created:
            await set_network(serial, enabled=False)
        await ensure_app(serial)
        if created:
            await apply_account_proxy(serial, account.account_id)
            await set_network(serial, enabled=True)
        signed_in = await sign_in(account, serial)
    except BaseException:
        await asyncio.shield(_undo(instance.index, name, launched=launched, created=created))
        raise
    return OpenMobileResult(account_id=account.account_id, status="started", signed_in=signed_in)


async def _undo(index: int, name: str, *, launched: bool, created: bool) -> None:
    """Stop what this start began; remove a container it created (best effort)."""
    try:
        if created:
            await remove_container(name)
        elif launched:
            await quit_instance(index)
    except EmulatorError as exc:
        logger.warning("container cleanup for %s failed (%s)", name, exc.code)


async def remove_account_container(account_id: str) -> None:
    """Delete a removed account's container, if it has one; never blocks the deletion."""
    try:
        removed = await remove_container(_container_name(account_id))
    except EmulatorError as exc:
        await log_event(
            "WARNING",
            "account_mobile_container_remove_failed",
            account_id=account_id,
            extra={"reason": exc.code},
        )
        return
    if removed:
        await log_event("INFO", "account_mobile_container_removed", account_id=account_id)
