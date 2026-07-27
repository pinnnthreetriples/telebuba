"""Quarantine re-check — the escalation ladder for a peer-flooded account.

``_recover_from_quarantine`` is the branch ``run_loop_iteration`` takes when a
quarantine window has elapsed: re-probe @SpamBot, resume on ``clean``, escalate
otherwise. Split from ``_loop`` for the file-size budget; ``_loop`` imports the
name back, so the quarantine branch reads exactly as it did before.

@SpamBot is reached via :mod:`services.warming._seams` (the module object, so a
``monkeypatch`` on ``services.warming._seams.refresh_spam_status`` still takes
effect here) and every write goes through the shared ``_set_state``.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from schemas.warming import WarmingCycleResult
from services.warming import _seams
from services.warming._state import _set_state

if TYPE_CHECKING:
    from datetime import datetime

    from schemas.warming import WarmingStateRecord


async def _recover_from_quarantine(
    account_id: str,
    record: WarmingStateRecord,
    now: datetime,
    *,
    run_id: str | None = None,
) -> WarmingCycleResult:
    """Re-check a quarantined account: resume if cleared, escalate otherwise.

    Called when a quarantine window has elapsed. Re-probes @SpamBot; only a
    ``clean`` verdict returns the account to warming. Anything else re-quarantines
    until the configured repeat cap, after which the account is given up on
    (``error`` + ERROR alert). An ``unknown`` verdict read nothing, so it does not
    release the hold — but it does spend a repeat, so the hold is BOUNDED, and it
    exhausts under ``quarantine_unreadable`` rather than claiming a confirmed flood.

    ``run_id`` (Round-2 P1 + Round-5 P1): if supplied, every write is
    CAS-guarded against the row's current run_id. A new CAS-write fires
    *before* ``refresh_spam_status`` so a stale loop does not issue the
    external @SpamBot probe on behalf of a generation that's already been
    replaced — the round-4 P1.2 fix only protected the regular cycle path,
    quarantine was still open.
    """
    warm = settings.warming
    # Round-5 P1: pre-probe CAS. Telegram I/O lives behind this gate.
    probe_started = await _set_state(
        account_id,
        "quarantine",
        last_event="quarantine_probe_started",
        heartbeat_at=now.isoformat(),
        quarantine_count=record.quarantine_count,
        expected_run_id=run_id,
    )
    if run_id is not None and not probe_started.applied:
        return WarmingCycleResult(account_id=account_id, status="skipped", detail="stale run")

    verdict = await _seams.refresh_spam_status(account_id, force=True)
    if verdict.status == "clean":
        next_run = (now + timedelta(seconds=warm.startup_jitter_max_seconds)).isoformat()
        await _set_state(
            account_id,
            "sleeping",
            last_event="quarantine_recovered",
            next_run_at=next_run,
            heartbeat_at=now.isoformat(),
            last_error=None,
            quarantine_count=0,
            expected_run_id=run_id,
        )
        await log_event("INFO", "warming_quarantine_recovered", account_id=account_id)
        return WarmingCycleResult(account_id=account_id, status="skipped", detail="recovered")

    # ``unknown`` is not a reading of the account's standing at all — a refused probe
    # (proxy down, dead session, @SpamBot silent, row deleted) — so it must NOT
    # release the quarantine. That fail-open let one proxy outage free every
    # quarantined account, once per cycle since probe errors stopped being cached.
    #
    # But holding it for FREE was the mirror failure, and unbounded.
    # ``SpamStatusKind`` is only ``clean | limited | unknown``: release needs
    # ``clean`` and escalation needed ``limited``, so ``unknown`` fell through here
    # with ``count = quarantine_count + 0`` — forever. ``services.spam_status``
    # returns an UNCACHED ``unknown`` for every probe that never reached @SpamBot, so
    # an account with a dead session or a dead proxy re-probed each window, got
    # ``unknown``, and held with the counter frozen: ``quarantine_exhausted``
    # unreachable, therefore ``state="error"`` and its ERROR-level alert unreachable
    # too, and ``quarantine`` on the board indefinitely at one WARNING per window.
    #
    # So EVERY window spends one repeat and the escalation REASON records which kind
    # spent the last one. ``quarantine_exhausted`` still asserts exactly what it
    # always did — a re-checked, still-standing flood — and ``quarantine_unreadable``
    # says the standing could not be read at all. Both park the account in ``error``
    # with an ERROR event, which is what puts it in front of the operator. The two
    # codes are logged from separate literal calls so
    # ``tests/test_logevent_i18n_parity`` can see both.
    still_limited = verdict.status == "limited"
    count = record.quarantine_count + 1
    if count >= warm.quarantine_max_repeats:
        await _set_state(
            account_id,
            "error",
            last_event="quarantine_exhausted" if still_limited else "quarantine_unreadable",
            last_error=(
                f"peer-flood not lifted after {count} checks"
                if still_limited
                else f"spam status unreadable after {count} checks"
            ),
            heartbeat_at=now.isoformat(),
            quarantine_count=count,
            expected_run_id=run_id,
        )
        if still_limited:
            await log_event(
                "ERROR",
                "warming_quarantine_exhausted",
                account_id=account_id,
                extra={"checks": count},
            )
        else:
            await log_event(
                "ERROR",
                "warming_quarantine_unreadable",
                account_id=account_id,
                extra={"checks": count},
            )
        return WarmingCycleResult(
            account_id=account_id,
            status="error",
            detail="quarantine exhausted" if still_limited else "quarantine unreadable",
        )

    next_run = (now + timedelta(hours=warm.quarantine_hours)).isoformat()
    await _set_state(
        account_id,
        "quarantine",
        last_event="quarantine_extended",
        next_run_at=next_run,
        heartbeat_at=now.isoformat(),
        quarantine_count=count,
        expected_run_id=run_id,
    )
    await log_event(
        "WARNING",
        "warming_quarantine_extended",
        account_id=account_id,
        extra={"checks": count},
    )
    return WarmingCycleResult(account_id=account_id, status="skipped", detail="quarantine extended")
