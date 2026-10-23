"""Prepare a mobile stint's phone when the stint starts, not at its first visit.

A phone stint's start first refuses a host that cannot run a phone at all (no docker,
no adb, a kernel without binder: ``core.emulator.host``), with the stint unchanged.
Then a background job readies everything else, so a problem shows on the card at
once instead of hours later at the first visit: the host check again, the screen's
scrcpy-server, the Telegram APK, and the pod (its images pulled or built on the way)
with the app installed and signed in through the account's existing session, parked
for the first visit. A visit waits while the job runs
(skipped as ``emulator_preparing``); a failed job leaves its code on the card, and a
visit that then warms clears it. A phone the operator holds is theirs: the job
stops there and the visits sign it in later. The job ends with the stint's loop task
(a stop, promote or restart cancels it), so no phone is readied for a stint that ended.

The jobs and their state are process-local, like the rest of the runtime state: after
a restart a visit prepares the phone itself (``open_account_mobile`` signs a
signed-out app in).
"""

from __future__ import annotations

import asyncio
from functools import partial
from typing import TYPE_CHECKING

from core.emulator import EmulatorError
from core.emulator.assets import ensure_scrcpy_server, ensure_telegram_apk
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
    from services.warming._steps import _ChannelTally

_PREPARING = "emulator_preparing"
_NOT_SIGNED_IN = "emulator_not_signed_in"
# Anything unexpected still ends the job: a phone left "preparing" would skip every visit.
_UNEXPECTED = "emulator_command_failed"
# A visit's detail when the app opened and ran but found nothing to do.
_NOTHING_TO_DO = "no activity"


async def prepare_phone(
    account_id: str, device_mode: DeviceType
) -> Callable[[asyncio.Task[None]], None]:
    """Refuse a host that cannot run a phone; how to start the phone job once the loop runs.

    The returned callable takes the stint's loop task and starts the account's phone
    job, which lives no longer than that loop: a stop, promote or restart cancels it
    with the loop (the card note goes too). A job still running for the account is
    waited out first. Nothing for a desktop stint.
    """
    if device_mode != "mobile":
        return _no_job
    try:
        await check_host()
    except EmulatorError as exc:
        raise WarmingDeviceUnavailableError(exc.code, account_id) from exc
    return partial(_start_job, account_id)


def _no_job(_loop: asyncio.Task[None]) -> None:
    return None


def _start_job(account_id: str, loop: asyncio.Task[None]) -> None:
    previous = _state.JOBS.get(account_id)
    _state.STATE[account_id] = PhonePrep(status="preparing")
    job = asyncio.create_task(_prepare_after(previous, account_id))
    _state.JOBS[account_id] = job
    job.add_done_callback(partial(_job_done, account_id))
    loop.add_done_callback(lambda _loop: job.cancel())


def _job_done(account_id: str, job: asyncio.Task[None]) -> None:
    if _state.JOBS.get(account_id) is not job:
        return  # a newer stint's job took over
    del _state.JOBS[account_id]
    if job.cancelled():
        _state.STATE.pop(account_id, None)


async def _prepare_after(previous: asyncio.Task[None] | None, account_id: str) -> None:
    if previous is not None:
        await asyncio.wait({previous})  # its pod cleanup first, never two opens at once
    await _prepare(account_id)


async def run_visit(
    request: WarmingCycleRequest,
    *,
    on_step: Callable[[str], Awaitable[None]],
    tally: _ChannelTally | None = None,
) -> WarmingCycleResult:
    """A mobile visit, skipped while its phone is being prepared.

    A visit that ran in the app clears a failed preparation's note: the phone works.
    """
    account_id = request.account_id
    if account_id in _state.JOBS:
        await log_event(
            "INFO",
            "warming_mobile_cycle_skipped",
            account_id=account_id,
            extra={"reason": _PREPARING},
        )
        return WarmingCycleResult(account_id=account_id, status="skipped", detail=_PREPARING)
    result = await run_mobile_cycle(request, on_step=on_step, tally=tally)
    if result.status in {"ok", "flood_wait"} or result.detail == _NOTHING_TO_DO:
        _state.STATE.pop(account_id, None)
    return result


async def _prepare(account_id: str) -> None:
    try:
        await check_host()
        await ensure_scrcpy_server()
        await ensure_telegram_apk()
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
