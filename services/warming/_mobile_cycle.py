"""A mobile stint's cycle: the account's Redroid pod instead of Telethon actions.

Taken only when the loop task is bound to the mobile device and the emulator is on
(``_loop._execute_cycle``); a desktop stint, or a mobile one with the emulator off,
keeps the Telethon cycle. One cycle opens the pod (booting it and signing the app
in when needed), spends the visit's time in the app, and stops the pod again,
whatever happened. A pod that cannot open or a session that fails is a failed cycle
with the refusal code as its detail: the loop schedules the next one as usual and
never falls back to Telethon.

The day's totals keep their columns: channels read go to ``reads``, reactions to
``reactions``; stories, scrolls and the seconds spent go to the log event only.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from schemas.warming import WarmingCycleResult
from services.accounts._result import AccountActionError
from services.accounts.mobile_emulator import (
    EmulatorBusyError,
    open_account_mobile,
    run_mobile_activity,
    stop_account_pod,
)
from services.warming._transitions import MOBILE_SESSION_ACTION

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from schemas.warming import WarmingCycleRequest

_NOT_SIGNED_IN = "emulator_not_signed_in"
# One budgeted action buys this many seconds in the app; a cycle gets at least
# the floor, and never more than the emulator's session cap.
_SECONDS_PER_ACTION = 30
_MIN_SECONDS = 60


def session_seconds(budget: int | None) -> int:
    """The seconds a visit with ``budget`` actions spends in the app."""
    cap = settings.emulator.activity_max_seconds
    if budget is None:
        return cap
    return min(cap, max(_MIN_SECONDS, budget * _SECONDS_PER_ACTION))


def _failed(account_id: str, code: str) -> WarmingCycleResult:
    return WarmingCycleResult(
        account_id=account_id,
        status="failed",
        detail=code,
        failures=1,
        last_failed_action=MOBILE_SESSION_ACTION,
    )


async def run_mobile_cycle(
    request: WarmingCycleRequest,
    *,
    on_step: Callable[[str], Awaitable[None]],
) -> WarmingCycleResult:
    """Open the pod, spend the visit in the app, stop the pod; never raises a refusal.

    A pod busy with the operator's own open or session is left running: it is theirs.
    """
    account_id = request.account_id
    seconds = session_seconds(request.remaining_actions)
    owned = True
    try:
        opened = await open_account_mobile(account_id)
        if not opened.signed_in:
            return await _log_failure(account_id, _NOT_SIGNED_IN)
        await on_step("read")
        activity = await run_mobile_activity(account_id, seconds)
    except EmulatorBusyError:
        owned = False
        return await _log_failure(account_id, "emulator_busy")
    except AccountActionError as exc:
        return await _log_failure(account_id, exc.code)
    finally:
        if owned:
            # Shielded: a Stop that cancels the cycle still lets the pod stop.
            await asyncio.shield(stop_account_pod(account_id))
    await log_event(
        "INFO",
        "warming_mobile_cycle_done",
        account_id=account_id,
        extra={"seconds": seconds, **activity.model_dump(mode="json")},
    )
    return WarmingCycleResult(
        account_id=account_id,
        status="ok",
        channels_read=activity.channels_read,
        reactions_sent=activity.reactions,
        attempted_actions=activity.channels_read + activity.reactions,
    )


async def _log_failure(account_id: str, code: str) -> WarmingCycleResult:
    await log_event(
        "WARNING", "warming_mobile_cycle_failed", account_id=account_id, extra={"reason": code}
    )
    return _failed(account_id, code)
