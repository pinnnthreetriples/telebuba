"""The per-account warming loop wrapper and its timing helpers.

Split out of :mod:`services.warming._runtime` to keep that module under the
file-size cap. ``_warming_loop`` is the long-running asyncio task body that
``start_warming`` / ``reconcile_warming_runtime`` create; the delay helpers and
the generation guard are only used by it (the helpers are also unit-tested).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_warming_state
from core.logging import log_event
from schemas.warming import is_warming
from services.warming import _seams
from services.warming._loop import run_loop_iteration
from services.warming._state import _set_state
from services.warming.pacing import (
    _SECONDS_PER_HOUR,
    _account_tz,
    _now_iso,
    _seconds_until,
    _shift_to_active_hours,
    persona_next_run_seconds,
)

if TYPE_CHECKING:
    from schemas.warming import WarmingStateRecord


def _is_live_generation(record: WarmingStateRecord | None, run_id: str | None) -> bool:
    """True iff ``record`` belongs to ``run_id`` and is still in a warming state.

    P1.2: ``run_id is None`` means the loop wasn't given a generation marker
    (legacy reconcile from a DB that pre-dates migration #8); we fall back to
    state-only checks so behaviour matches the pre-P1.2 baseline.
    """
    if record is None:
        return False
    if not is_warming(record.state) or record.state == "error":
        return False
    if run_id is None:
        return True
    return record.run_id == run_id


def _loop_sleep_seconds(record: WarmingStateRecord | None, now: datetime) -> float:
    """Seconds to wait before the next cycle, from the persisted ``next_run_at``.

    Falls back to a fresh persona-paced gap only if the schedule is missing
    (it never should be after ``run_loop_iteration`` writes one).
    """
    if record is not None and record.next_run_at is not None:
        return _seconds_until(record.next_run_at, now)
    persona = record.activity_persona if record is not None else "normal"
    return persona_next_run_seconds(persona, 0, _seams.rng)


async def _initial_delay_seconds(
    account_id: str,
    record: WarmingStateRecord | None,
    now: datetime,
) -> float:
    """Delay before the first cycle after (re)starting a loop.

    Honours a persisted future ``next_run_at`` so a restart resumes the existing
    schedule. A cold-started account (no schedule yet) picks its first run at a
    random point across ``cold_start_spread_hours`` and then routes it through the
    active-hours window — so a bulk onboarding of N accounts neither all fires at
    once nor kicks off in the middle of the night.
    """
    if record is not None and record.next_run_at is not None:
        return _seconds_until(record.next_run_at, now)
    candidate = now + timedelta(
        seconds=_seams.rng.uniform(
            0.0, settings.warming.cold_start_spread_hours * _SECONDS_PER_HOUR
        ),
    )
    candidate = _shift_to_active_hours(
        candidate, await _account_tz(account_id), _seams.rng, account_id
    )
    return _seconds_until(candidate.isoformat(), now)


async def _persist_cold_start_schedule(
    account_id: str,
    record: WarmingStateRecord | None,
    delay: float,
    now: datetime,
    run_id: str | None,
) -> None:
    """Persist the first-cycle time so the card can show a real pre-start countdown.

    A cold start (no persisted ``next_run_at``) otherwise waits up to
    ``cold_start_spread_hours`` with the row's ``next_run_at`` still null, so the
    board can't tell "waiting for the first cycle" apart from "mid-subscribe" and
    shows no countdown. Writing the computed time here (guarded by the generation
    CAS) fixes both, and lets a restart resume the same schedule instead of
    re-rolling the wait.
    """
    if record is not None and record.next_run_at is not None:
        return
    await _set_state(
        account_id,
        "active",
        next_run_at=(now + timedelta(seconds=delay)).isoformat(),
        expected_run_id=run_id,
    )


async def _warming_loop(
    account_id: str,
    *,
    run_id: str | None = None,
) -> None:  # pragma: no cover - long-running task
    """Run cycles forever, timing each from the persisted ``next_run_at``.

    Never raises to the caller. On (re)start it respects an existing schedule so
    an app restart does not turn parked accounts into an activity spike.

    ``run_id`` is the generation marker the caller stamped before creating this
    task. The loop refuses to keep running if the DB ``run_id`` no longer
    matches (= a newer ``start_warming`` minted a fresh generation), and passes
    it to ``run_loop_iteration`` so an in-flight cycle won't write through after
    a restart either (P1.2).

    Round-6 P1: the crash handler also runs the generation check + CAS. Without
    it, a stale loop that crashed after a restart would stamp ``error`` over
    the new generation's row, undoing the restart.
    """
    try:
        record = await fetch_warming_state(account_id)
        if not _is_live_generation(record, run_id):
            return
        now = datetime.now(UTC)
        delay = await _initial_delay_seconds(account_id, record, now)
        await _persist_cold_start_schedule(account_id, record, delay, now, run_id)
        await asyncio.sleep(delay)
        while True:
            record = await fetch_warming_state(account_id)
            if not _is_live_generation(record, run_id):
                break
            await run_loop_iteration(account_id, run_id=run_id)
            record = await fetch_warming_state(account_id)
            if not _is_live_generation(record, run_id):
                break
            await asyncio.sleep(_loop_sleep_seconds(record, datetime.now(UTC)))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - a background loop must never crash silently.
        await log_event(
            "ERROR",
            "warming_loop_crashed",
            account_id=account_id,
            extra={"error_type": type(exc).__name__, "message": str(exc)},
        )
        # Round-6 P1: pre-check generation so a stale crash does not even try
        # to write. The CAS predicate below is the suspenders — if our pre-read
        # raced a restart, the upsert still refuses to overwrite a fresh
        # generation's row.
        latest = await fetch_warming_state(account_id)
        if not _is_live_generation(latest, run_id):
            return
        await _set_state(
            account_id,
            "error",
            last_event="loop_crashed",
            last_error=f"{type(exc).__name__}: {exc}",
            heartbeat_at=_now_iso(),
            expected_run_id=run_id,
        )
