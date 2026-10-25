"""Independent emulators: phones bound to no account, each on a pool proxy of its own.

* a phone is a row (its Android identity, its proxy) and, once started, the same pod
  a mobile account has (``core.emulator.pod``), named ``standalone_prefix`` + its id;
* a start refuses what cannot work before any container is touched — the feature
  switched off, no proxy or one the tunnel cannot use; it boots the pod on the
  CURRENT proxy with that proxy country's timezone and carrier, installs Telegram and
  seeds the first boot, and signs nothing in: the operator works it on the web screen;
* a running pod is used as it stands only while its gateway runs the current proxy;
* a proxy change, or the proxy's deletion, stops the pod; deleting the phone removes
  its pod and ``/data`` diff, then its row; a pod that cannot be removed is logged and
  never keeps the row (``remove_account_container`` treats an account's the same);
* one start at a time per phone; an operator holding its controls excludes a start.

The holds are process-local, like the accounts' (one uvicorn worker).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_proxy, fetch_proxy_settings
from core.device_fingerprint_android import generate_android_fingerprint
from core.emulator import (
    EmulatorError,
    adb_serial,
    apply_stealth_profile,
    assert_sdk,
    connect_device,
    container_spec,
    ensure_app,
    ensure_telegram_apk,
    find_instance,
    pod_runs_proxy,
    remove_pod,
    seed_device_if_needed,
    start_pod,
    stop_pod,
)
from core.logging import log_event
from core.repositories.standalone_emulators import (
    create_standalone_emulator,
    delete_standalone_emulator,
    fetch_standalone_emulator,
    list_standalone_ids_for_proxy,
    set_standalone_proxy,
)
from services.accounts._mobile_start import _drop, _undo, _wait_boot, set_battery
from services.accounts._result import AccountActionError
from services.accounts.mobile_emulator import EmulatorBusyError, EmulatorRefusedError

if TYPE_CHECKING:
    from core.emulator import ContainerSpec
    from schemas.device_farm import PhoneStatus
    from schemas.emulator import EmulatorRefusalCode
    from schemas.proxy import ProxySettings
    from schemas.standalone_emulator import StandaloneEmulator, StandaloneEmulatorCreate

_DISABLED: EmulatorRefusalCode = "mobile_emulator_disabled"
_UNKNOWN_PHONE: EmulatorRefusalCode = "emulator_not_found"
_NOT_RUNNING: EmulatorRefusalCode = "emulator_not_running"
_PROXY_MISSING: EmulatorRefusalCode = "emulator_proxy_missing"
_PROXY_UNSUPPORTED: EmulatorRefusalCode = "emulator_proxy_unsupported"

_LOCKS: dict[str, asyncio.Lock] = {}
# Phones an operator controls by hand on the web screen.
_MANUAL: set[str] = set()
# Starts in flight, kept so the event loop does not drop them half way.
_STARTS: set[asyncio.Task[None]] = set()


def standalone_name(emulator_id: str) -> str:
    """The phone's container name, which is also its phone id."""
    return f"{settings.emulator.standalone_prefix}{emulator_id}"


def standalone_of(phone_id: str) -> str | None:
    """The emulator a phone id names; ``None`` when it names no independent emulator."""
    emulator_id = phone_id.removeprefix(settings.emulator.standalone_prefix)
    return emulator_id if emulator_id and emulator_id != phone_id else None


def standalone_activity(emulator_id: str) -> PhoneStatus | None:
    """What holds the phone's pod right now, as far as this process knows."""
    lock = _LOCKS.get(emulator_id)
    if lock is not None and lock.locked():
        return "starting"
    return "manual" if emulator_id in _MANUAL else None


def claim_standalone(emulator_id: str) -> None:
    """Hand the phone's controls to an operator; ``emulator_busy`` while a start holds it."""
    if standalone_activity(emulator_id) is not None:
        raise EmulatorBusyError
    _MANUAL.add(emulator_id)


def release_standalone(emulator_id: str) -> None:
    _MANUAL.discard(emulator_id)


async def require_standalone(emulator_id: str) -> StandaloneEmulator:
    emulator = await fetch_standalone_emulator(emulator_id)
    if emulator is None:
        raise EmulatorRefusedError(_UNKNOWN_PHONE)
    return emulator


async def create_standalone(data: StandaloneEmulatorCreate) -> StandaloneEmulator:
    """A new phone with a fresh Android identity, on ``data.proxy_id`` when given.

    Nothing is started: the pod is made by the phone's first start. An unknown proxy
    is a ``ValueError``, a full one ``ProxyCapacityError``.
    """
    emulator_id = uuid.uuid4().hex
    label = (data.label or "").strip() or None
    fingerprint = generate_android_fingerprint(emulator_id, None)
    emulator = await create_standalone_emulator(emulator_id, label, data.proxy_id, fingerprint)
    await log_event("INFO", "standalone_emulator_created", extra={"emulator_id": emulator_id})
    return emulator


async def _proxy_for(emulator: StandaloneEmulator) -> tuple[ProxySettings, str | None]:
    """The proxy to start the pod on and its country, or why there is none."""
    if not settings.emulator.enabled:
        raise EmulatorRefusedError(_DISABLED)
    if emulator.proxy_id is None:
        raise EmulatorRefusedError(_PROXY_MISSING)
    proxy = await fetch_proxy(emulator.proxy_id)
    if proxy is None:
        raise EmulatorRefusedError(_PROXY_MISSING)
    if proxy.proxy_type != "socks5":
        raise EmulatorRefusedError(_PROXY_UNSUPPORTED)
    proxy_settings = await fetch_proxy_settings(emulator.proxy_id)
    if proxy_settings is None:
        raise EmulatorRefusedError(_PROXY_MISSING)
    return proxy_settings, proxy.country_code


async def start_standalone(emulator_id: str) -> None:
    """Start the phone in the background; refused at once when it cannot start now."""
    await require_standalone(emulator_id)
    if not settings.emulator.enabled:
        raise EmulatorRefusedError(_DISABLED)
    if standalone_activity(emulator_id) is not None:
        raise EmulatorBusyError
    task = asyncio.create_task(_start_in_background(emulator_id))
    _STARTS.add(task)
    task.add_done_callback(_STARTS.discard)
    await asyncio.sleep(0)  # let the open take the phone's lock before answering


async def _start_in_background(emulator_id: str) -> None:
    try:
        await open_standalone(emulator_id)
    except AccountActionError:
        return  # logged by the open itself (``standalone_emulator_open_failed``)
    except Exception as exc:  # noqa: BLE001 - a background task has nobody to raise to
        await log_event(
            "WARNING",
            "standalone_emulator_open_failed",
            extra={"emulator_id": emulator_id, "reason": type(exc).__name__},
        )


async def open_standalone(emulator_id: str) -> str:
    """Start (or find running on its current proxy) the phone's pod; its adb serial."""
    lock = _LOCKS.setdefault(emulator_id, asyncio.Lock())
    if lock.locked() or emulator_id in _MANUAL:
        raise EmulatorBusyError
    try:
        async with lock:
            emulator = await require_standalone(emulator_id)
            proxy, country = await _proxy_for(emulator)
            spec = container_spec(emulator.fingerprint, country)
            serial = await _open(emulator_id, spec, proxy)
    except (EmulatorError, AccountActionError) as exc:
        await log_event(
            "WARNING",
            "standalone_emulator_open_failed",
            extra={"emulator_id": emulator_id, "reason": exc.code},
        )
        if isinstance(exc, EmulatorError):
            raise EmulatorRefusedError(exc.code) from exc
        raise
    await log_event("INFO", "standalone_emulator_opened", extra={"emulator_id": emulator_id})
    return serial


async def _open(emulator_id: str, spec: ContainerSpec, proxy: ProxySettings) -> str:
    name = standalone_name(emulator_id)
    instance = await find_instance(name)
    if instance is not None and instance.running:
        if await pod_runs_proxy(name, proxy):
            serial = adb_serial(instance.index)
            await connect_device(serial)
            await set_battery(serial, emulator_id, spec.region.timezone)
            return serial
        await stop_pod(name)
    return await _start(emulator_id, spec, proxy, new=instance is None)


async def _start(emulator_id: str, spec: ContainerSpec, proxy: ProxySettings, *, new: bool) -> str:
    """Boot the stopped (or ``new``) pod on ``proxy``, Telegram installed, first boot seeded."""
    name = standalone_name(emulator_id)
    # A new container has no Telegram yet: its APK is fetched while the pod starts.
    apk = asyncio.create_task(ensure_telegram_apk()) if new else None
    try:
        instance, fresh_data = await start_pod(name, spec, proxy)  # undoes itself on failure
        booted = False
        try:
            serial = adb_serial(instance.index)
            await _wait_boot(serial, apk)
            await assert_sdk(serial, spec.sdk)
            await apply_stealth_profile(serial)
            booted = True
            await set_battery(serial, emulator_id, spec.region.timezone)
            await ensure_app(serial)
            await _seed(emulator_id, serial, spec)
        except BaseException:
            remove = apk is not None and fresh_data and not booted
            await asyncio.shield(_undo(name, remove=remove))
            raise
    finally:
        await _drop(apk)
    return serial


async def _seed(emulator_id: str, serial: str, spec: ContainerSpec) -> None:
    """Give a never-seeded phone its contacts and photos; never fails the start."""
    try:
        await seed_device_if_needed(serial, spec)
    except Exception as exc:  # noqa: BLE001 - seeding must never fail the start
        reason = exc.code if isinstance(exc, EmulatorError) else type(exc).__name__
        await log_event(
            "WARNING",
            "standalone_emulator_seed_failed",
            extra={"emulator_id": emulator_id, "reason": reason},
        )


async def standalone_serial(emulator_id: str) -> str:
    """The adb serial of the phone's running pod; ``emulator_not_running`` otherwise."""
    await require_standalone(emulator_id)
    try:
        instance = await find_instance(standalone_name(emulator_id))
    except EmulatorError as exc:
        raise EmulatorRefusedError(exc.code) from exc
    if instance is None or not instance.running:
        raise EmulatorRefusedError(_NOT_RUNNING)
    return adb_serial(instance.index)


async def _stop_pod(emulator_id: str) -> None:
    """Stop the phone's pod, after a start in flight; a failure is logged, never raised."""
    if not settings.emulator.enabled:
        return
    lock = _LOCKS.setdefault(emulator_id, asyncio.Lock())
    try:
        async with lock:
            await stop_pod(standalone_name(emulator_id))
    except EmulatorError as exc:
        await log_event(
            "WARNING",
            "standalone_emulator_stop_failed",
            extra={"emulator_id": emulator_id, "reason": exc.code},
        )


async def stop_standalone(emulator_id: str) -> None:
    """Put the phone to sleep (its ``/data`` stays); refused while it starts."""
    await require_standalone(emulator_id)
    if standalone_activity(emulator_id) == "starting":
        raise EmulatorBusyError
    await _stop_pod(emulator_id)


async def set_standalone_phone_proxy(emulator_id: str, proxy_id: str | None) -> None:
    """Move the phone to ``proxy_id`` (``None``: off its proxy) and stop its pod.

    The next start brings it up on the new proxy. An unknown proxy is a ``ValueError``,
    a full one ``ProxyCapacityError``.
    """
    if not await set_standalone_proxy(emulator_id, proxy_id):
        raise EmulatorRefusedError(_UNKNOWN_PHONE)
    await _stop_pod(emulator_id)
    await log_event(
        "INFO",
        "standalone_emulator_proxy_set",
        extra={"emulator_id": emulator_id, "proxy_id": proxy_id},
    )


async def standalone_ids_on_proxy(proxy_id: str) -> list[str]:
    """The phones on ``proxy_id``, read before its deletion takes them off it."""
    return await list_standalone_ids_for_proxy(proxy_id)


async def stop_standalone_pods(emulator_ids: list[str]) -> None:
    """Stop these phones' pods (their proxy is gone); never fails the caller."""
    for emulator_id in emulator_ids:
        await _stop_pod(emulator_id)


async def delete_standalone(emulator_id: str) -> None:
    """Remove the phone's pod and ``/data`` diff, then the phone itself.

    Waits for a start in flight, so it cannot create a pod after this one is gone.
    """
    await require_standalone(emulator_id)
    lock = _LOCKS.setdefault(emulator_id, asyncio.Lock())
    _MANUAL.discard(emulator_id)
    try:
        async with lock:
            try:
                await remove_pod(standalone_name(emulator_id))
            except EmulatorError as exc:
                await log_event(
                    "WARNING",
                    "standalone_emulator_remove_failed",
                    extra={"emulator_id": emulator_id, "reason": exc.code},
                )
            await delete_standalone_emulator(emulator_id)
    finally:
        if _LOCKS.get(emulator_id) is lock:
            del _LOCKS[emulator_id]
    await log_event("INFO", "standalone_emulator_deleted", extra={"emulator_id": emulator_id})
