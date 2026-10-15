"""Quarantine re-check — what happens when a quarantine rest has run out.

``_recover_from_quarantine`` is the branch ``run_loop_iteration`` takes when a
quarantine window has elapsed: re-ask @SpamBot, then

* ``clean`` — back to warming, with the cap ramping up and DMs shut for a while (so
  is a ``limited`` answer for an account whose block is already known and kept);
* ``limited`` — a spam block rarely passes within a rest, so after ``quarantine_max_repeats``
  still-limited answers (one by default) warming stops in ``spam_blocked``
  (``services.warming._spam_block``) for the operator to delete or keep the account;
  below that count the account rests another window;
* ``unknown`` — the probe read nothing (proxy down, dead session, @SpamBot silent), so
  it is not a re-check of the block at all and spends none. Holding for free would be
  unbounded, though, so unreadable windows count in the row's unreadable streak, and
  past ``quarantine_unreadable_max`` of them the account parks in ``error`` with an
  ERROR event (``quarantine_unreadable``) — what puts it in front of the operator.

@SpamBot is reached via :mod:`services.warming._seams` (the module object, so a
``monkeypatch`` on ``services.warming._seams.refresh_spam_status`` still takes
effect here) and every write goes through the shared ``_set_state``.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import record_spam_check_outcome
from core.logging import log_event
from schemas.warming import WarmingCycleResult, spam_block_known
from services.warming import _seams
from services.warming._spam_block import stop_spam_blocked
from services.warming._state import _set_state

if TYPE_CHECKING:
    from datetime import datetime

    from schemas.warming import WarmingStateRecord

_STALE = "stale run"


def _stale(account_id: str) -> WarmingCycleResult:
    return WarmingCycleResult(account_id=account_id, status="skipped", detail=_STALE)


async def _release(account_id: str, now: datetime, run_id: str | None) -> WarmingCycleResult:
    next_run = (now + timedelta(seconds=settings.warming.startup_jitter_max_seconds)).isoformat()
    write = await _set_state(
        account_id,
        "sleeping",
        last_event="quarantine_recovered",
        next_run_at=next_run,
        heartbeat_at=now.isoformat(),
        last_error=None,
        quarantine_count=0,
        # Starts the post-release daily-cap ramp and DM block (``compute_intensity``).
        recovered_at=now.isoformat(),
        expected_run_id=run_id,
    )
    if run_id is not None and not write.applied:
        return _stale(account_id)
    await log_event("INFO", "warming_quarantine_recovered", account_id=account_id)
    return WarmingCycleResult(account_id=account_id, status="skipped", detail="recovered")


async def _extend(
    record: WarmingStateRecord,
    now: datetime,
    checks: int,
    run_id: str | None,
) -> WarmingCycleResult:
    """Rest another quarantine window; ``checks`` is the limited answers spent so far."""
    account_id = record.account_id
    write = await _set_state(
        account_id,
        "quarantine",
        last_event="quarantine_extended",
        next_run_at=(now + timedelta(hours=settings.warming.quarantine_hours)).isoformat(),
        heartbeat_at=now.isoformat(),
        quarantine_count=checks,
        expected_run_id=run_id,
    )
    if run_id is not None and not write.applied:
        return _stale(account_id)
    await log_event(
        "WARNING",
        "warming_quarantine_extended",
        account_id=account_id,
        extra={"checks": checks},
    )
    return WarmingCycleResult(account_id=account_id, status="skipped", detail="quarantine extended")


async def _park_unreadable(
    account_id: str,
    now: datetime,
    streak: int,
    run_id: str | None,
) -> WarmingCycleResult:
    write = await _set_state(
        account_id,
        "error",
        last_event="quarantine_unreadable",
        last_error=f"spam status unreadable after {streak} checks",
        heartbeat_at=now.isoformat(),
        expected_run_id=run_id,
    )
    if run_id is not None and not write.applied:
        return _stale(account_id)
    await log_event(
        "ERROR",
        "warming_quarantine_unreadable",
        account_id=account_id,
        extra={"checks": streak},
    )
    return WarmingCycleResult(account_id=account_id, status="error", detail="quarantine unreadable")


async def _unreadable(
    record: WarmingStateRecord,
    now: datetime,
    run_id: str | None,
) -> WarmingCycleResult:
    """An unreadable re-check: spend none of the block's re-checks, but stay bounded."""
    streak = await record_spam_check_outcome(
        record.account_id, readable=False, expected_run_id=run_id
    )
    if streak is None:
        return _stale(record.account_id)
    if streak >= settings.warming.quarantine_unreadable_max:
        return await _park_unreadable(record.account_id, now, streak, run_id)
    return await _extend(record, now, record.quarantine_count, run_id)


async def _reset_streak(account_id: str, run_id: str | None) -> bool:
    """End the unreadable streak on a reading; ``False`` when a newer run owns the row."""
    streak = await record_spam_check_outcome(account_id, readable=True, expected_run_id=run_id)
    return streak is not None


async def _recover_from_quarantine(
    account_id: str,
    record: WarmingStateRecord,
    now: datetime,
    *,
    run_id: str | None = None,
) -> WarmingCycleResult:
    """Re-check a quarantined account: resume if cleared, stop on a confirmed block.

    ``run_id``: if supplied, every write is CAS-guarded against the row's current
    run_id. The first CAS write fires *before* ``refresh_spam_status`` so a stale loop
    does not issue the external @SpamBot probe on behalf of a generation that has
    already been replaced.
    """
    probe_started = await _set_state(
        account_id,
        "quarantine",
        last_event="quarantine_probe_started",
        heartbeat_at=now.isoformat(),
        quarantine_count=record.quarantine_count,
        expected_run_id=run_id,
    )
    if run_id is not None and not probe_started.applied:
        return _stale(account_id)

    verdict = await _seams.refresh_spam_status(account_id, force=True)
    if verdict.status == "unknown":
        return await _unreadable(record, now, run_id)
    # A reading ends the unreadable streak; a refused guard means a newer run.
    if record.spam_unknown_streak > 0 and not await _reset_streak(account_id, run_id):
        return _stale(account_id)
    # A kept spam-blocked account rests out a peer flood like any other, but its
    # ``limited`` is the known block, not news: it goes back to warming (DMs stay shut).
    if verdict.status == "clean" or spam_block_known(record):
        return await _release(account_id, now, run_id)
    checks = record.quarantine_count + 1
    if checks >= settings.warming.quarantine_max_repeats:
        return await stop_spam_blocked(record, now, checks, run_id)
    return await _extend(record, now, checks, run_id)
