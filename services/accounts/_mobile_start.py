"""Start a stopped or new mobile pod and sign its Telegram app in (``mobile_emulator``'s open).

* a new container's Telegram APK is fetched while the pod starts and boots: one that
  cannot be had fails the start then, not after the boot and the seeding (a container
  found on the host reads its app off the phone and needs no APK);
* a container's first boot gives it contacts and photos (``core.emulator._seeder``)
  alongside the app's start; a seeding that fails is logged, never fails the open;
* on any failure the pod is stopped again, and removed only when it is new, its
  ``/data`` too, and failed by its boot: a booted one keeps its seeded ``/data`` for the
  next start, and a shared ``/data`` an earlier pod left (a sign-in) is never removed;
* the operator's sign-in that stops short (no login code to read, a cooldown, a screen
  it did not expect) is not a failure of the open: it is logged, and the pod is left
  up on its screen with the refusal, for the operator to finish the sign-in by hand.
  Warming's background open still fails: nobody watches its pod;
* a phone started without its ``app`` (the device farm's) fetches no APK, and installs,
  starts and signs in nothing: it is booted, seeded and shown, and handed over as it is;
* each stage is reported as it begins (``_mobile_progress``) for the operator's dialog.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo

from core.emulator import (
    EmulatorError,
    adb_serial,
    apply_battery_telemetry,
    apply_stealth_profile,
    assert_sdk,
    bring_app_to_front,
    ensure_app,
    ensure_telegram_apk,
    read_screen,
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
from services.accounts._mobile_progress import report_stage
from services.accounts.devices import DeviceRefusedError

if TYPE_CHECKING:
    from core.android_apk import AppIdentity
    from core.emulator import ContainerSpec
    from core.emulator.ui_dump import Screen
    from schemas.accounts import AccountRead
    from schemas.proxy import ProxySettings

# Stdlib sink, codes and step names only (see ``core.emulator._errors``).
logger = logging.getLogger(__name__)

# Who opens the pod: the operator (in front, signed in), warming (in the background,
# only an entry screen signed in), or the device farm (booted alone, the app untouched).
type OpenMode = Literal["operator", "warming", "boot"]

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
    mode: OpenMode,
) -> tuple[OpenMobileResult, str]:
    """Start the account's stopped (or ``new``) pod on ``proxy``, sign its app in; its serial.

    The device farm's ``boot`` only boots the pod: no APK, no install, no sign-in.
    """
    name = container_name(account.account_id)
    # A new container has no Telegram yet: its APK is fetched while the pod starts.
    apk = asyncio.create_task(ensure_telegram_apk()) if new and mode != "boot" else None
    try:
        instance, fresh_data = await start_pod(name, spec, proxy)  # undoes itself on failure
        serial = adb_serial(instance.index)
        try:
            shown = None if mode == "warming" else instance.index
            await _boot(account.account_id, serial, spec, apk, show_on=shown)
        except BaseException:
            await asyncio.shield(_undo(name, remove=new and fresh_data))
            raise
    finally:
        await _drop(apk)
    try:
        signed_in, refusal = await _bring_up(account, serial, spec, mode)
    except BaseException:
        await asyncio.shield(_undo(name, remove=False))
        raise
    return OpenMobileResult(
        account_id=account.account_id,
        status="started",
        signed_in=signed_in,
        sign_in_refusal=refusal,
    ), serial


async def _boot(
    account_id: str,
    serial: str,
    spec: ContainerSpec,
    apk: asyncio.Task[AppIdentity] | None,
    *,
    show_on: int | None,
) -> None:
    """Boot Android (with a new container's APK), show it, check its SDK, hide the tells."""
    report_stage(account_id, "boot")
    await _wait_boot(serial, apk)
    if show_on is not None:
        await show(container_name(account_id), show_on)
    await assert_sdk(serial, spec.sdk)
    await apply_stealth_profile(serial)


async def _bring_up(
    account: AccountRead, serial: str, spec: ContainerSpec, mode: OpenMode
) -> tuple[bool, str | None]:
    """A booted pod's battery, seeding and (but for ``boot``) its app signed in."""
    await set_battery(serial, account.account_id, spec.region.timezone)
    if mode != "boot":
        report_stage(account.account_id, "app")
    seeding = asyncio.create_task(_seed(account.account_id, serial, spec))
    try:
        signed_in, refusal = False, None
        if mode != "boot":
            background = mode == "warming"
            signed_in, refusal = await _app_signed_in(account, serial, background=background)
        await seeding
    finally:
        await _drop(seeding)
    return signed_in, refusal


async def _app_signed_in(
    account: AccountRead, serial: str, *, background: bool
) -> tuple[bool, str | None]:
    """Install and start the app, then sign it in: ``(signed in, why it stopped short)``."""
    await ensure_app(serial)
    # In the background only an entry screen is signed in; in front, always.
    screen = (await wait_screen(serial, _KNOWN_SCREENS))[0] if background else None
    if screen is not None and screen not in ENTRY_SCREENS:
        return screen == "chats", None
    return await sign_in_or_hand_over(account, serial, background=background)


async def front_screen(serial: str) -> Screen:
    """The screen a running app shows once it is put in front."""
    await bring_app_to_front(serial)
    return (await read_screen(serial))[0]


async def set_battery(serial: str, account_id: str, timezone: str) -> None:
    """Show the battery the phone has at this hour of its ``timezone`` (``_battery``).

    Applied at every boot and every visit to a running pod, so the level moves through
    the phone's day and night. A phone that refuses it is left as it is: never a reason to fail
    the open.
    """
    now = datetime.now(ZoneInfo(timezone))
    with suppress(EmulatorError):
        await apply_battery_telemetry(serial, account_id=account_id, now=now)


async def sign_in_or_hand_over(
    account: AccountRead, serial: str, *, background: bool
) -> tuple[bool, str | None]:
    """Sign the app in: ``(signed in, why it stopped short)``.

    The operator's refusal is logged and handed back with the pod left up, to be
    finished by hand on its screen; warming's is raised (its pod is stopped).
    """
    if background:
        return await sign_in(account, serial), None
    try:
        return await sign_in(account, serial), None
    except (EmulatorError, DeviceRefusedError) as exc:
        await log_event(
            "WARNING",
            "account_mobile_sign_in_failed",
            account_id=account.account_id,
            extra={"reason": exc.code},
        )
        return False, exc.code


async def _wait_boot(serial: str, apk: asyncio.Task[AppIdentity] | None) -> None:
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

    Runs alongside the app's install and sign-in (they read no contacts). Anything but
    a cancellation is logged and the sign-in goes on; with no marker, the next boot retries.
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
    """Stop the pod this start began; ``remove`` it (new data that failed by its boot).

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
