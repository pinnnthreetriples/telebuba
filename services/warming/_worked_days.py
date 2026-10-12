"""Warming days are days of work.

A local date counts toward the stint once: on the first cycle that did real
warming work, or on the account's planned quiet day (resting is part of the plan).
A day spent in error, quarantine or a flood cooldown earns nothing, so an account
cannot reach its target on the calendar alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.db import count_warmed_day
from core.logging import log_event

if TYPE_CHECKING:
    from schemas.warming import WarmingCycleResult


def _cycle_worked(result: WarmingCycleResult) -> bool:
    """A clean cycle, or a failed one that still landed a warming action."""
    if result.status == "ok":
        return True
    if result.status != "failed":
        return False
    landed = (
        result.channels_joined + result.channels_read + result.reactions_sent + result.messages_sent
    )
    return landed > 0


async def _count_worked_day(account_id: str, local_date: str, *, run_id: str | None) -> None:
    """Count ``local_date`` for the stint, at most once, and say so when it lands."""
    total = await count_warmed_day(account_id, local_date, expected_run_id=run_id)
    if total is None:
        return
    await log_event(
        "INFO",
        "warming_day_counted",
        account_id=account_id,
        extra={"warmed_days": total, "date": local_date},
    )


async def _count_cycle_day(
    account_id: str,
    result: WarmingCycleResult,
    local_date: str,
    *,
    run_id: str | None,
) -> None:
    """Count the day of a settled cycle when that cycle did real work."""
    if _cycle_worked(result):
        await _count_worked_day(account_id, local_date, run_id=run_id)
