"""Daily-budget reservation hand-back — split from ``services.warming._loop``.

Owns the two functions that give back the unspent part of the pre-cycle daily
reservation (#208): the CAS-guarded write itself and the never-raising wrapper the
loop's abnormal-exit handler uses. Split out for the file-size budget; both are
imported by :mod:`services.warming._loop` so the call sites are unaffected.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from core.db import fetch_warming_state
from core.logging import log_event
from services.warming._state import _set_state

if TYPE_CHECKING:
    from schemas.warming import WarmingState

# Stdlib sink for full third-party text — see ``core.proxy_check._failed_result``.
logger = logging.getLogger(__name__)


async def _reconcile_reservation(
    account_id: str,
    state: WarmingState,
    daily: tuple[int, str],
    spent: int,
    *,
    run_id: str | None,
) -> None:
    """Hand back the unspent part of the pre-cycle reservation (#208).

    The ``cycle_started`` write books the whole remaining daily budget before the
    cycle spends any of it, and ``_finalize_after_cycle`` normally reconciles that
    down to the real spend. Every exit that does NOT reach that write — a
    ``CancelledError`` from shutdown / ``stop_warming`` / a restart's
    cancel-and-replace, a raising cycle, or a finalize that finds the row is no
    longer ours — would otherwise leave the whole budget booked and park the
    account on a phantom "daily limit" until the next UTC midnight, forfeiting the
    rest of the day's warming.

    Writes ``daily_actions`` only (``state`` is the row's current state, echoed
    back so an ``error``/``sleeping`` row is not resurrected as ``active``), and
    stays CAS-guarded: a newer generation's row, or one the operator already
    stopped, is left alone. ``Exception`` failures are swallowed — never
    propagated — because this runs on the way out of another exception and must
    never mask it, but they are always logged: a silent reconcile failure is the
    forfeited day this function exists to prevent. A ``CancelledError`` is
    deliberately NOT swallowed (``except Exception``, not ``BaseException``):
    every caller sits inside the loop's own abnormal-exit handler, which logs it
    and re-raises the original exception, so eating it here would only break
    asyncio's contract — the finalize call site has no other backstop.
    """
    daily_count, daily_date = daily
    try:
        # ``shield``: ``shutdown_warming_runtime`` cancels a second time when its
        # 5s ``gather`` times out, and that second cancel lands on this write's
        # ``to_thread``. An abandoned write leaves the full reservation booked and
        # replaces whatever exception was propagating — a genuine crash would then
        # look like a cancellation to ``_runner`` and never park the account.
        write = await asyncio.shield(
            _set_state(
                account_id,
                state,
                daily_actions=daily_count + spent,
                daily_count_date=daily_date,
                expected_run_id=run_id,
            ),
        )
        if not write.applied:
            # The CAS refused (a newer generation owns the row, or a stop already
            # wrote ``idle``): the reservation stays booked and nothing else will
            # clear it before the next UTC midnight, so this is the case the
            # operator actually has to see — not a successful hand-back.
            await log_event(
                "WARNING",
                "warming_reservation_stranded",
                account_id=account_id,
                extra={"spent": spent, "daily_actions": daily_count + spent},
            )
        elif spent:
            # A forfeited day is otherwise indistinguishable from a legitimate park:
            # ``_gate_daily_limit`` writes the same ``last_event="daily_limit"`` either
            # way, so without this the fleet can lose a day of warming per deploy in
            # total silence. Silent when nothing was spent (cancelled while queued on
            # the semaphore), or every deploy would log one WARNING per account.
            await log_event(
                "WARNING",
                "warming_reservation_reconciled",
                account_id=account_id,
                extra={"spent": spent, "daily_actions": daily_count + spent},
            )
    except Exception as exc:
        # Name the failure: a bare log here loses the only clue to why the day was
        # forfeited, and the traceback alone does not survive log aggregation.
        logger.exception("reservation reconcile failed for %s (%s)", account_id, type(exc).__name__)


async def _release_reservation_on_exit(
    account_id: str,
    daily: tuple[int, str],
    spent: int,
    *,
    run_id: str | None,
) -> None:
    """Hand the reservation back while an exception unwinds ``run_loop_iteration``.

    Never raises: the caller re-raises the original exception unchanged, and a
    failure here must not replace it.
    """
    try:
        # Echo the row's own state back rather than a literal ``active``: a
        # readiness park or a stop may have moved it while the cycle ran. A row
        # that is gone entirely (``remove_account`` proceeds after its 5s cancel
        # wait, so ``delete_account`` can purge it while we are still here) holds
        # no reservation to hand back, and the FK would reject the insert anyway
        # — same skip as ``_finalize_after_cycle``'s call site.
        latest = await fetch_warming_state(account_id)
        if latest is not None:
            try:
                await _reconcile_reservation(account_id, latest.state, daily, spent, run_id=run_id)
            except asyncio.CancelledError:
                # Scoped to the reconcile call alone: its write is shielded, so one
                # already in flight still lands — hence WARNING without a traceback.
                # A cancel on the read above never reached that write, so the whole
                # reservation is stranded and stays an ERROR below. (A cancel landing
                # on the reconcile's own ``stranded`` WARNING also reads as
                # "interrupted": by then the write is final, only its report is lost.)
                logger.warning("reservation reconcile interrupted for %s", account_id)
    except BaseException as exc:
        # Same reason as above, and here the type is the whole signal: a
        # ``CancelledError`` on the read is a shutdown, anything else is a real fault.
        logger.exception("reservation reconcile failed for %s (%s)", account_id, type(exc).__name__)
