"""What the gateway writes to the ledger around one probe: the state after it, the row, the pruning.

Split out of the gateway for its size. Nothing here reads the clock (the caller passes the
instant, so the gateway's one clock stays the one tests patch) and nothing logs: the events are
the gateway's.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.repositories.spam_probe_ledger import (
    fetch_probe_state,
    finish_attempt,
    prune_attempts,
    save_probe_outcome,
)
from schemas.spam_intel import ProbeAttemptFinish, ProbeState
from services.spam_intel._policy import backoff_until

if TYPE_CHECKING:
    from datetime import datetime

    from services.spam_intel._answers import Ask, Ended

logger = logging.getLogger(__name__)


async def remember(req: Ask, ended: Ended) -> None:
    """Fold one finished attempt into the account's state: streak, backoff, last failure.

    The streak and the last success are taken from the row AS IT IS NOW, not from the snapshot
    the budget read before the probe: a probe takes minutes, and the history sweep may have
    written its mark meanwhile (it owns its own column, which this write leaves alone).

    A probe that CRASHED or was CANCELLED never gets here (:func:`finish_quietly` closes its row
    as ``error``): that says nothing about the account, so it adds no failure streak and no
    backoff. The ledger row still counts toward every cap, and the coalescing window reads the
    row's outcome (``services.spam_intel._answers.coalesced``) instead of trusting a reading
    stored before the dead attempt.
    """
    previous = await fetch_probe_state(req.account_id)
    readable = ended.label in {"clean", "limited"}
    streak = 0 if readable else (previous.fail_streak if previous else 0) + 1
    stamp = ended.at.isoformat()
    last_success = stamp if readable else (previous.last_success_at if previous else None)
    next_allowed = (
        None if readable else backoff_until(streak, ended.at, settings.spam_intel).isoformat()
    )
    await save_probe_outcome(
        ProbeState(
            account_id=req.account_id,
            last_attempt_at=stamp,
            last_success_at=last_success,
            fail_streak=streak,
            next_allowed_at=next_allowed,
            last_caller=req.caller,
            last_outcome=ended.label,
            last_error=ended.error,
            updated_at=stamp,
        )
    )


async def finish_quietly(attempt_id: int, error: str, at: datetime, clock: float) -> None:
    """Close a ledger row as ``error`` (an exception class name); never raises."""
    try:
        await finish_attempt(
            ProbeAttemptFinish(
                attempt_id=attempt_id,
                finished_at=at.isoformat(),
                outcome="error",
                error_type=error,
                duration_ms=int((time.monotonic() - clock) * 1000),
            )
        )
    except Exception as exc:  # noqa: BLE001 - closing a ledger row must not mask the real error.
        logger.warning("spam attempt finish failed: %s", type(exc).__name__)


async def prune(at: datetime) -> None:
    """Drop ledger rows past the retention days and the row cap; never raises."""
    intel = settings.spam_intel
    cutoff = (at - timedelta(days=intel.attempts_retention_days)).isoformat()
    try:
        await prune_attempts(cutoff, intel.attempts_max_rows)
    except Exception as exc:  # noqa: BLE001 - housekeeping must not fail a probe.
        logger.warning("spam attempt prune failed: %s", type(exc).__name__)
