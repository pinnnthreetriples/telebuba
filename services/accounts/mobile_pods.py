"""The phone pods no task owns: left up by a crash or a restart, or up at shutdown.

``stop_orphaned_pods`` runs at startup, before any open of this run; ``shutdown_mobile_pods``
after warming drained, where a cancelled cycle's own shielded stop may not finish
before the loop closes. Neither ever fails the app's start or stop: a host without
docker has no pod, and any other failure is logged.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.config import settings
from core.emulator import EmulatorError, stop_running_pods

if TYPE_CHECKING:
    from schemas.emulator import EmulatorRefusalCode

__all__ = ["shutdown_mobile_pods", "stop_orphaned_pods"]

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
