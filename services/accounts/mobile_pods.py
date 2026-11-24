"""Pods between visits, and the pods no task owns.

``park_account_pod`` freezes an account's pod after a warming visit (``docker pause``),
to be thawed by its next open; warming decides how long a parked pod may wait.

Pods that outlive what they belong to: ``stop_pods_off_proxy`` stops the ones whose gateway
does not run their account's current proxy (a proxy changed while a stop failed or was
cut short), and ``retry_pending_pod_removals`` removes, by tombstone (``pending_cleanups``,
written before an account's removal is tried), the pods whose removal failed. Neither
infers anything from what the database lacks, and neither ever raises.

The pods no task owns, left up by a crash or a restart, or up at shutdown:
``stop_orphaned_pods`` runs at startup, before any open of this run; ``shutdown_mobile_pods``
after warming drained, where a cancelled cycle's own shielded stop may not finish
before the loop closes. Neither ever fails the app's start or stop: a host without
docker has no pod, and any other failure is logged.

``prefetch_phone_files`` fetches a missing Telegram APK and scrcpy-server in the
background at startup, so a mobile fingerprint minted later already claims the APK's
own version; a fetch that fails is logged and tried again where the file is needed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_account, fetch_account_proxy_settings
from core.emulator import (
    EmulatorError,
    list_instances,
    pause_pod,
    pod_runs_proxy,
    remove_pod,
    stop_paused_pod,
    stop_pod,
    stop_running_pods,
)
from core.emulator.assets import ensure_microg, ensure_scrcpy_server, ensure_telegram_apk
from core.logging import log_event
from core.repositories.pending_cleanups import clear_pending_cleanup, fetch_pending_cleanups
from services.accounts._pod_cleanup import POD_CLEANUP_KIND, cancel_pending_stops
from services.accounts.mobile_emulator import _LOCKS, container_name

if TYPE_CHECKING:
    from schemas.emulator import EmulatorRefusalCode

__all__ = [
    "park_account_pod",
    "prefetch_phone_files",
    "retry_pending_pod_removals",
    "shutdown_mobile_pods",
    "stop_orphaned_pods",
    "stop_parked_pod",
    "stop_pods_off_proxy",
]

_EMULATOR_NOT_FOUND: EmulatorRefusalCode = "emulator_not_found"

# Stdlib sink, codes only (see ``core.emulator._errors``).
logger = logging.getLogger(__name__)

# Strong reference to the startup fetch; the event loop holds tasks weakly.
_FETCHES: set[asyncio.Task[None]] = set()


async def _stop_all_pods(moment: str) -> None:
    if not settings.emulator.enabled:
        return
    try:
        stopped = await stop_running_pods()
    except EmulatorError as exc:
        if exc.code != _EMULATOR_NOT_FOUND:
            logger.warning("stopping the pods at %s failed (%s)", moment, exc.code)
        return
    if stopped:
        logger.info("stopped %s pod container(s) at %s", stopped, moment)


async def stop_orphaned_pods() -> None:
    """Stop the pods a crashed or restarted run left up."""
    await _stop_all_pods("startup")


def prefetch_phone_files() -> None:
    """Start fetching the phone's missing files; nothing with the emulator off."""
    if not settings.emulator.enabled:
        return
    task = asyncio.create_task(_fetch_phone_files())
    _FETCHES.add(task)
    task.add_done_callback(_FETCHES.discard)


async def _fetch_phone_files() -> None:
    for fetch in (ensure_telegram_apk, ensure_scrcpy_server, ensure_microg):
        try:
            await fetch()
        except EmulatorError as exc:
            await log_event("WARNING", "emulator_files_fetch_failed", extra={"reason": exc.code})


async def shutdown_mobile_pods() -> None:
    """Stop every pod still running as the app goes down."""
    await cancel_pending_stops()
    await _stop_all_pods("shutdown")


async def park_account_pod(account_id: str) -> bool:
    """Freeze the account's pod after a visit; ``False`` when it was not running or failed.

    Waits for a start in flight like a stop does. A pause that fails is logged: the
    caller stops the pod instead.
    """
    lock = _LOCKS.setdefault(account_id, asyncio.Lock())
    try:
        async with lock:
            return await pause_pod(container_name(account_id))
    except EmulatorError as exc:
        await log_event(
            "WARNING",
            "account_mobile_pod_pause_failed",
            account_id=account_id,
            extra={"reason": exc.code},
        )
        return False


async def stop_parked_pod(account_id: str) -> None:
    """Stop the account's pod if it is still parked; one opened since is left running."""
    lock = _LOCKS.setdefault(account_id, asyncio.Lock())
    try:
        async with lock:
            await stop_paused_pod(container_name(account_id))
    except EmulatorError as exc:
        await log_event(
            "WARNING",
            "account_mobile_pod_stop_failed",
            account_id=account_id,
            extra={"reason": exc.code},
        )


async def _off_its_proxy(account_id: str, name: str) -> bool:
    """Whether the account's pod runs no proxy of its account's (gone, or another one)."""
    proxy = await fetch_account_proxy_settings(account_id)
    return proxy is None or not await pod_runs_proxy(name, proxy)


async def _stop_if_off_proxy(account_id: str, name: str) -> bool:
    """Stop the pod when it is off its proxy; one an open or a stop holds the lock of is left."""
    lock = _LOCKS.setdefault(account_id, asyncio.Lock())
    if lock.locked():
        return False  # an open in flight is mid-start, a stop in flight is this very thing
    async with lock:
        if not await _off_its_proxy(account_id, name):
            return False
        await stop_pod(name)
    return True


async def stop_pods_off_proxy() -> int:
    """Stop every running account pod whose gateway does not run its account's proxy; how many.

    Also a pod whose account has no proxy any more. For the stop a proxy change could not
    do (it failed, or the process was cut short): a pod keeps its app online through the
    OLD address until then. Never raises; a pod that cannot be stopped is tried next time.
    """
    if not settings.emulator.enabled:
        return 0
    prefix = settings.emulator.instance_prefix
    stopped = 0
    try:
        instances = await list_instances()
    except EmulatorError:
        return 0
    for instance in instances:
        if not (instance.running or instance.paused) or not instance.name.startswith(prefix):
            continue
        try:
            stopped += await _stop_if_off_proxy(instance.name.removeprefix(prefix), instance.name)
        except Exception as exc:  # noqa: BLE001 - a guard sweep must never raise
            logger.warning("pod %s off its proxy was not stopped (%s)", instance.name, _why(exc))
    if stopped:
        await log_event("WARNING", "account_mobile_pod_off_proxy_stopped", extra={"count": stopped})
    return stopped


def _why(exc: Exception) -> str:
    return exc.code if isinstance(exc, EmulatorError) else type(exc).__name__


async def _retry_removal(key: str) -> bool:
    """Remove the pod a tombstone names (or drop the tombstone); whether it is settled."""
    account_id = key.removeprefix(settings.emulator.instance_prefix)
    try:
        # An account of that id exists again: its pod is its own, never this tombstone's.
        if await fetch_account(account_id) is None:
            await remove_pod(key)  # ``False``: there is no pod, which is as good as removed
        await clear_pending_cleanup(POD_CLEANUP_KIND, key)
    except Exception as exc:  # noqa: BLE001 - kept for the next retry
        await log_event(
            "WARNING",
            "account_mobile_container_remove_failed",
            account_id=account_id,
            extra={"reason": _why(exc), "retry": True},
        )
        return False
    return True


async def retry_pending_pod_removals() -> int:
    """Remove the pods whose removal failed and left a tombstone; how many were settled.

    Only what a tombstone (``pending_cleanups``, kind ``pod``) names is ever removed, never a
    pod for lack of an account: a wrong or empty database must not delete phones. Never
    raises; a removal that fails keeps its tombstone.
    """
    if not settings.emulator.enabled:
        return 0
    try:
        keys = await fetch_pending_cleanups(POD_CLEANUP_KIND)
    except Exception as exc:  # noqa: BLE001 - startup must not fail on this
        logger.warning("pending pod removals were not read (%s)", _why(exc))
        return 0
    settled = sum([await _retry_removal(key) for key in keys])
    if settled:
        await log_event("INFO", "account_mobile_pending_removals_settled", extra={"count": settled})
    return settled
