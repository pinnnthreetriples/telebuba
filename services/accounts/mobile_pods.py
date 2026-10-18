"""Pods between visits, and the pods no task owns.

``park_account_pod`` freezes an account's pod after a warming visit (``docker pause``),
to be thawed by its next open; warming decides how long a parked pod may wait.

The pods no task owns, left up by a crash or a restart, or up at shutdown:
``stop_orphaned_pods`` runs at startup, before any open of this run; ``shutdown_mobile_pods``
after warming drained, where a cancelled cycle's own shielded stop may not finish
before the loop closes. Neither ever fails the app's start or stop: a host without
docker has no pod, and any other failure is logged.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from core.config import settings
from core.emulator import EmulatorError, pause_pod, stop_paused_pod, stop_running_pods
from core.logging import log_event
from services.accounts.mobile_emulator import _LOCKS, _container_name

if TYPE_CHECKING:
    from schemas.emulator import EmulatorRefusalCode

__all__ = [
    "park_account_pod",
    "shutdown_mobile_pods",
    "stop_orphaned_pods",
    "stop_parked_pod",
]

_EMULATOR_NOT_FOUND: EmulatorRefusalCode = "emulator_not_found"

# Stdlib sink, codes only (see ``core.emulator._errors``).
logger = logging.getLogger(__name__)


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


async def shutdown_mobile_pods() -> None:
    """Stop every pod still running as the app goes down."""
    await _stop_all_pods("shutdown")


async def park_account_pod(account_id: str) -> bool:
    """Freeze the account's pod after a visit; ``False`` when it was not running or failed.

    Waits for a start in flight like a stop does. A pause that fails is logged: the
    caller stops the pod instead.
    """
    lock = _LOCKS.setdefault(account_id, asyncio.Lock())
    try:
        async with lock:
            return await pause_pod(_container_name(account_id))
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
            await stop_paused_pod(_container_name(account_id))
    except EmulatorError as exc:
        await log_event(
            "WARNING",
            "account_mobile_pod_stop_failed",
            account_id=account_id,
            extra={"reason": exc.code},
        )
