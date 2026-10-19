"""Prepare a mobile stint's phone when the stint starts, not at its first visit.

A phone stint's start first refuses a host that cannot run a phone at all (no docker,
no adb, a kernel without binder: ``core.emulator.host``), with the stint unchanged.
Then a background job readies everything else, so a problem shows on the card at
once instead of hours later at the first visit: the host's binder probe (building the
gateway image for it), the screen's scrcpy-server, the pod (its images pulled or built
on the way) with the Telegram app installed and signed in through the account's
existing session, parked for the first visit. A visit waits while the job runs
(skipped as ``emulator_preparing``); a failed job leaves its code on the card, and a
visit that then warms clears it. A phone the operator holds is theirs: the job
stops there and the visits sign it in later.

The jobs and their state are process-local, like the rest of the runtime state: after
a restart a visit prepares the phone itself (``open_account_mobile`` signs a
signed-out app in).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from core.emulator import EmulatorError
from core.emulator.assets import ensure_scrcpy_server
from core.emulator.host import check_host
from core.logging import log_event
from schemas.warming import PhonePrep, WarmingCycleResult
from services.accounts._result import AccountActionError
from services.accounts.mobile_emulator import EmulatorBusyError
from services.warming import _mobile_prep_state as _state
from services.warming._mobile_cycle import prepare_pod, run_mobile_cycle
from services.warming._start_state import WarmingDeviceUnavailableError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from schemas.device_fingerprint import DeviceType
    from schemas.warming import WarmingCycleRequest

_PREPARING = "emulator_preparing"
_NOT_SIGNED_IN = "emulator_not_signed_in"
# Anything unexpected still ends the job: a phone left "preparing" would skip every visit.
_UNEXPECTED = "emulator_command_failed"


async def prepare_phone(account_id: str, device_mode: DeviceType) -> None:
    """Refuse a host that cannot run a phone, then start the account's phone job.

    Nothing for a desktop stint; a job already running is left to finish.
    """
    if device_mode != "mobile":
        return
    try:
        await check_host(build_probe=False)
    except EmulatorError as exc:
        raise WarmingDeviceUnavailableError(exc.code, account_id) from exc
    if account_id in _state.JOBS:
        return
    _state.STATE[account_id] = PhonePrep(status="preparing")
    job = asyncio.create_task(_prepare(account_id))
    _state.JOBS[account_id] = job
    job.add_done_callback(lambda _done: _state.JOBS.pop(account_id, None))


async def run_visit(
    request: WarmingCycleRequest, *, on_step: Callable[[str], Awaitable[None]]
) -> WarmingCycleResult:
    """A mobile visit, skipped while its phone is being prepared."""
    account_id = request.account_id
    if account_id in _state.JOBS:
        await log_event(
            "INFO",
            "warming_mobile_cycle_skipped",
            account_id=account_id,
            extra={"reason": _PREPARING},
        )
        return WarmingCycleResult(account_id=account_id, status="skipped", detail=_PREPARING)
    result = await run_mobile_cycle(request, on_step=on_step)
    if result.status == "ok":
        _state.STATE.pop(account_id, None)
    return result


async def _prepare(account_id: str) -> None:
    try:
        await check_host(build_probe=True)
        await ensure_scrcpy_server()
        signed_in = await prepare_pod(account_id)
    except EmulatorBusyError:
        _state.STATE.pop(account_id, None)
        return
    except (EmulatorError, AccountActionError) as exc:
        await _fail(account_id, exc.code)
        return
    except Exception as exc:  # noqa: BLE001 - a crashed job must not hold the visits back
        await _fail(account_id, _UNEXPECTED, error_type=type(exc).__name__)
        return
    if not signed_in:
        await _fail(account_id, _NOT_SIGNED_IN)
        return
    _state.STATE.pop(account_id, None)
    await log_event("INFO", "warming_phone_ready", account_id=account_id)


async def _fail(account_id: str, code: str, *, error_type: str | None = None) -> None:
    _state.STATE[account_id] = PhonePrep(status="failed", code=code)
    extra: dict[str, object] = {"reason": code}
    if error_type is not None:
        extra["error_type"] = error_type
    await log_event("WARNING", "warming_phone_prep_failed", account_id=account_id, extra=extra)
