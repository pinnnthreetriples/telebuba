"""Open a mobile account in its local Android container (redroid under Docker).

The container is the account's MOBILE device made visible: it is created with the
mobile fingerprint's manufacturer and model, refused when its Android SDK differs,
and its Telegram app connects straight to the account's SOCKS5 proxy. The I/O lives
in ``core.emulator``; the policy lives here:

* refuse what cannot work before any container is touched — the feature switched
  off, no mobile device, no proxy or a proxy the app cannot use, or a stored 2FA
  password ``input text`` cannot type (found out at the 2FA screen, it would cost a
  login code already sent);
* one start at a time per account (a second one answers ``emulator_busy``);
* every boot a start makes has its network cut (from the host right after launch,
  then inside Android) and gets it back only once the app's proxy is set, so its
  first connection already goes through the proxy; the setting is the app's own and
  outlives the container's scrcpy window being closed;
* the sign-in state is read off the screen every time, never remembered;
* a running container that shows the app's intro or phone screen is signed in where
  it stands; one showing the chat list, or anything else, is left alone;
* on any failure the container this call started is stopped again, and removed when
  this call created it, so the next attempt redoes the network-off first start.

The per-account locks are process-local, like the rest of the runtime state (one
uvicorn worker). A start takes the lock before it reads the account, and only the
account's deletion drops it: a start never does, because a deletion queued on that
same lock would otherwise share the account with the next start's fresh lock.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_account_twofa_password, fetch_device_fingerprint
from core.emulator import (
    EmulatorError,
    adb_serial,
    apply_account_proxy,
    assert_sdk,
    bring_app_to_front,
    connect_device,
    container_spec,
    create_instance,
    ensure_app,
    escape_input_text,
    find_instance,
    launch_instance,
    quit_instance,
    read_screen,
    remove_container,
    set_container_network,
    set_network,
    show_instance,
    wait_for_boot,
)
from core.logging import log_event
from schemas.emulator import OpenMobileResult
from services.accounts._emulator_sign_in import sign_in
from services.accounts._result import AccountActionError
from services.accounts.devices import DeviceRefusedError
from services.accounts.lifecycle import require_account

if TYPE_CHECKING:
    from core.emulator.ui_dump import Screen
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

# A running app on one of these still needs signing in; anything else is left as is.
_ENTRY_SCREENS: set[Screen] = {"intro", "phone"}


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
    password = await fetch_account_twofa_password(account.account_id)
    if password:
        try:
            escape_input_text(password)
        except EmulatorError as exc:
            raise EmulatorRefusedError(exc.code) from exc
    return fingerprint


async def open_account_mobile(account_id: str) -> OpenMobileResult:
    """Start (or find running) the account's container and sign its Telegram app in."""
    lock = _LOCKS.setdefault(account_id, asyncio.Lock())
    if lock.locked():
        raise EmulatorBusyError
    try:
        async with lock:
            account = await require_account(account_id)
            fingerprint = await _refuse_what_cannot_work(account)
            result = await _open(account, fingerprint)
    except EmulatorError as exc:
        await log_event(
            "WARNING",
            "account_mobile_open_failed",
            account_id=account_id,
            extra={"reason": exc.code},
        )
        raise EmulatorRefusedError(exc.code) from exc
    await log_event("INFO", "account_mobile_opened", account_id=account_id)
    return result


async def _open(account: AccountRead, fingerprint: DeviceFingerprint) -> OpenMobileResult:
    spec = container_spec(fingerprint)
    name = _container_name(account.account_id)
    instance = await find_instance(name)
    if instance is not None and instance.running:
        # Up already, so an earlier start booted it, and every boot a start makes
        # applies the proxy offline before the network comes back: it is not booted
        # or reconfigured again. Only an entry screen is acted on, through its nodes.
        serial = adb_serial(instance.index)
        await connect_device(serial)
        await show_instance(instance.index)
        await bring_app_to_front(serial)
        screen, _nodes = await read_screen(serial)
        signed_in = screen == "chats"
        if screen in _ENTRY_SCREENS:
            signed_in = await sign_in(account, serial)
        return OpenMobileResult(
            account_id=account.account_id,
            status="already_running",
            signed_in=signed_in,
        )
    created = instance is None
    try:
        if instance is None:
            instance = await create_instance(name, spec)
        serial = adb_serial(instance.index)
        await launch_instance(instance.index)
        # Every boot this call makes goes offline until the app's own SOCKS5 setting
        # names the account's CURRENT proxy: nothing about an existing container is
        # trusted, not even that an earlier start got as far as setting it. The host
        # side is cut at once, before Android is up (adb cannot reach it yet), so an
        # app the boot starts by itself cannot connect; ``svc`` follows once it can.
        await set_container_network(instance.index, online=False)
        await wait_for_boot(serial)
        await show_instance(instance.index)
        await assert_sdk(serial, spec.sdk)
        await set_network(serial, enabled=False)
        await ensure_app(serial)
        await apply_account_proxy(serial, account.account_id)
        await set_container_network(instance.index, online=True)
        await set_network(serial, enabled=True)
        signed_in = await sign_in(account, serial)
    except BaseException:
        index = instance.index if instance is not None else None
        await asyncio.shield(_undo(index, name, created=created))
        raise
    return OpenMobileResult(account_id=account.account_id, status="started", signed_in=signed_in)


async def _undo(index: int | None, name: str, *, created: bool) -> None:
    """Stop the container this start booted; remove it when this start created it."""
    try:
        if created:
            await remove_container(name)
        elif index is not None:
            await quit_instance(index)
    except EmulatorError as exc:
        logger.warning("container cleanup for %s failed (%s)", name, exc.code)


async def remove_account_container(account_id: str) -> None:
    """Delete a removed account's container, if it has one; never blocks the deletion.

    Waits for a start of the same account in flight, so that start cannot create a
    container after this one is gone. It alone drops the account's lock: the account
    row is already deleted, so a later start finds no account under a fresh lock.
    """
    lock = _LOCKS.setdefault(account_id, asyncio.Lock())
    try:
        async with lock:
            removed = await remove_container(_container_name(account_id))
    except EmulatorError as exc:
        await log_event(
            "WARNING",
            "account_mobile_container_remove_failed",
            account_id=account_id,
            extra={"reason": exc.code},
        )
        return
    finally:
        if _LOCKS.get(account_id) is lock:
            del _LOCKS[account_id]
    if removed:
        await log_event("INFO", "account_mobile_container_removed", account_id=account_id)
