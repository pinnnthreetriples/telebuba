"""Start a stopped or new mobile pod and sign its Telegram app in (``mobile_emulator``'s open).

* a new container's Telegram APK is fetched while the pod starts and boots: one that
  cannot be had fails the start then, not after the boot and the seeding (a container
  found on the host reads its app off the phone and needs no APK);
* a container's first boot gives it contacts and photos (``core.emulator._seeder``)
  before the app starts; a seeding that fails is logged, never fails the open;
* on any failure the pod is stopped again, and removed only when it is new and failed
  by its boot: a booted one keeps its seeded ``/data`` for the next start.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from core.emulator import (
    EmulatorError,
    adb_serial,
    assert_sdk,
    ensure_app,
    ensure_telegram_apk,
    remove_pod,
    seed_device_if_needed,
    show_instance,
    start_pod,
    stop_pod,
    wait_for_boot,
    wait_screen,
)
from core.logging import log_event
from schemas.emulator import OpenMobileResult
from services.accounts._emulator_sign_in import sign_in
from services.accounts._mobile_holds import container_name

if TYPE_CHECKING:
    from core.android_apk import AppIdentity
    from core.emulator import ContainerSpec
    from core.emulator.ui_dump import Screen
    from schemas.accounts import AccountRead
    from schemas.proxy import ProxySettings

# Stdlib sink, codes and step names only (see ``core.emulator._errors``).
logger = logging.getLogger(__name__)

# A running app on one of these still needs signing in; anything else is left as is.
ENTRY_SCREENS: set[Screen] = {"intro", "phone"}
# A background open waits for the app to show one of these, then reads it as it is.
_KNOWN_SCREENS: set[Screen] = {"chats", "intro", "phone", "confirm", "code", "password"}


async def show(name: str, index: int) -> None:
    """Open the pod's window; one that cannot open is logged and the open goes on."""
    try:
        await show_instance(name, index)
    except EmulatorError as exc:
        logger.warning("pod window %s did not open (%s)", index, exc.code)


async def start_and_sign_in(
    account: AccountRead,
    spec: ContainerSpec,
    proxy: ProxySettings,
    *,
    new: bool,
    background: bool,
) -> OpenMobileResult:
    """Start the account's stopped (or ``new``) pod on ``proxy`` and sign its app in."""
    # A new container has no Telegram yet: its APK is fetched while the pod starts.
    apk = asyncio.create_task(ensure_telegram_apk()) if new else None
    try:
        return await _start(account, spec, proxy, apk=apk, background=background)
    finally:
        await _drop(apk)


async def _start(
    account: AccountRead,
    spec: ContainerSpec,
    proxy: ProxySettings,
    *,
    apk: asyncio.Task[AppIdentity] | None,
    background: bool,
) -> OpenMobileResult:
    name = container_name(account.account_id)
    instance = await start_pod(name, spec, proxy)  # undoes itself when it fails
    booted = False
    try:
        serial = adb_serial(instance.index)
        await _boot(serial, apk)
        if not background:
            await show(name, instance.index)
        await assert_sdk(serial, spec.sdk)
        booted = True
        await _seed(account.account_id, serial, spec)
        await ensure_app(serial)
        if background:
            screen, _nodes = await wait_screen(serial, _KNOWN_SCREENS)
            signed_in = screen == "chats"
            if screen in ENTRY_SCREENS:
                signed_in = await sign_in(account, serial)
        else:
            signed_in = await sign_in(account, serial)
    except BaseException:
        await asyncio.shield(_undo(name, remove=apk is not None and not booted))
        raise
    return OpenMobileResult(account_id=account.account_id, status="started", signed_in=signed_in)


async def _boot(serial: str, apk: asyncio.Task[AppIdentity] | None) -> None:
    """Wait for Android and, for a new container, its APK; the first failure ends both."""
    boot = asyncio.ensure_future(wait_for_boot(serial))
    pending = {boot, *([] if apk is None else [apk])}
    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
    finally:
        await _drop(boot)


async def _drop[T](task: asyncio.Future[T] | None) -> None:
    """Cancel ``task`` when it still runs and wait for it; its failure is not raised."""
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _seed(account_id: str, serial: str, spec: ContainerSpec) -> None:
    """Give a never-seeded container its contacts and photos; never fails the open.

    Seeding only makes the phone look used: a failure is logged and the sign-in goes
    on. The marker is written only once both are in, so the next boot tries again.
    Anything but a cancellation is caught: a broken name file or a full host disk
    must not stop the sign-in either.
    """
    try:
        await seed_device_if_needed(serial, spec)
    except Exception as exc:  # noqa: BLE001 - seeding must never fail the open
        reason = exc.code if isinstance(exc, EmulatorError) else type(exc).__name__
        await log_event(
            "WARNING",
            "account_mobile_seed_failed",
            account_id=account_id,
            extra={"reason": reason},
        )


async def _undo(name: str, *, remove: bool) -> None:
    """Stop the pod this start began; ``remove`` it (a new one that failed by its boot).

    A new container that booted is kept stopped: its seeded ``/data`` (and any app
    already in) spare the next start a first boot and a seeding.
    """
    try:
        if remove:
            await remove_pod(name)
        else:
            await stop_pod(name)
    except EmulatorError as exc:
        logger.warning("pod cleanup for %s failed (%s)", name, exc.code)
