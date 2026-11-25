"""What a pod is checked against before the operator's open hands it over.

* ``refuse_if_host_full``: the operator's start of a pod (their open, the device farm's
  boot) obeys ``EMULATOR__MAX_RUNNING_PODS`` like warming does, counting what Docker
  really runs (warming's own count never sees the operator's pods), and refuses a pod
  the Docker host's memory cannot carry (``EMULATOR__HOST_MEMORY_FRACTION`` of its total,
  ``EMULATOR__MEMORY_MB`` per pod). A host whose memory cannot be read blocks nothing:
  the count limit still applies. A pod the account already has running is not asked: it
  is not a new one.
* ``verify_pod_gateway``: a pod that already exists was trusted for as long as it lived.
  Its gateway's barrier is checked again (``core.emulator.verify_gateway``) at the start
  of every use and a failure is journalled. With ``EMULATOR__GATEWAY_CHECK_ENFORCE`` the
  pod is also stopped and the open refused (fail closed); without it (the default until
  the check has been watched on real pods) the pod is still used.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.config import settings
from core.emulator import (
    EmulatorError,
    GatewayUnhealthyError,
    host_memory_bytes,
    list_instances,
    stop_pod,
    verify_gateway,
)
from core.logging import log_event

if TYPE_CHECKING:
    from schemas.emulator import EmulatorRefusalCode

__all__ = ["refuse_if_host_full", "verify_pod_gateway"]

# Stdlib sink, codes only (see ``core.emulator._errors``).
logger = logging.getLogger(__name__)

_BYTES_PER_MB = 1024 * 1024
_EMULATOR_HOST_MEMORY: EmulatorRefusalCode = "emulator_host_memory"
_EMULATOR_POD_LIMIT: EmulatorRefusalCode = "emulator_pod_limit"


async def refuse_if_host_full(name: str) -> None:
    """Refuse starting pod ``name`` past the pod limit or the host's memory share.

    ``emulator_pod_limit`` when as many other pods as the limit are up already (running or
    frozen: a parked pod keeps its memory), ``emulator_host_memory`` when one more would
    take more of the host's memory than ``host_memory_fraction`` allows.
    """
    up = [i for i in await list_instances() if (i.running or i.paused) and i.name != name]
    if len(up) >= settings.emulator.max_running_pods:
        raise EmulatorError(_EMULATOR_POD_LIMIT)
    total = await host_memory_bytes()
    if total is None:
        return
    needed = (len(up) + 1) * settings.emulator.memory_mb * _BYTES_PER_MB
    if needed > settings.emulator.host_memory_fraction * total:
        raise EmulatorError(_EMULATOR_HOST_MEMORY)


async def verify_pod_gateway(account_id: str, name: str) -> None:
    """Re-check the barrier of the account's EXISTING pod ``name`` and journal a failure.

    The event carries the stable reason code only: no proxy address, credentials or
    output. When the check is enforced the pod is stopped and the refusal
    (``emulator_gateway_unhealthy``, the error the check raised) goes to the caller; a
    stop that fails is logged, never hides the refusal. A check that fails to run at all
    (another ``EmulatorError``) is journalled as ``check_failed`` and treated the same way.
    """
    try:
        await verify_gateway(name)
    except EmulatorError as exc:
        # A check that could not run (no such gateway, docker failing) is a failed check too:
        # in observe mode it must never abort the open, enforced it refuses like the rest.
        unhealthy = (
            exc if isinstance(exc, GatewayUnhealthyError) else GatewayUnhealthyError("check_failed")
        )
        enforced = settings.emulator.gateway_check_enforce
        if enforced:
            try:
                await stop_pod(name)
            except EmulatorError as stop_failure:
                logger.warning("unhealthy pod %s was not stopped (%s)", name, stop_failure.code)
        await log_event(
            "WARNING",
            "emulator_gateway_unhealthy",
            account_id=account_id,
            extra={"reason": unhealthy.reason, "enforced": enforced},
        )
        if enforced:
            raise unhealthy from exc
