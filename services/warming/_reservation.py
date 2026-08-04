"""Daily-budget reservation hand-back — split from ``services.warming._loop``.

Owns the functions that give back the unspent part of the pre-cycle daily
reservation (#208): the booking arithmetic, the guarded write itself, and the
never-raising wrapper the loop's abnormal-exit handler uses. Split out for the
file-size budget; all three are imported by :mod:`services.warming._loop` so the
call sites are unaffected.
"""

from __future__ import annotations

import asyncio
import logging
from typing import NamedTuple

from core.db import fetch_warming_state, hand_back_warming_reservation
from core.logging import log_event

# Stdlib sink for full third-party text — see ``core.proxy_check._failed_result``.
logger = logging.getLogger(__name__)


class _Reservation(NamedTuple):
    """Today's ``(count, date)`` plus the budget the pre-cycle write booked on top.

    The three travel together from the ``cycle_started`` write to the hand-back
    because the hand-back's guard is the booked value itself — carrying them apart
    is how the release ends up unable to name the booking it is releasing.
    """

    daily_count: int
    daily_date: str
    remaining: int | None

    @property
    def booked(self) -> int:
        """What the row carries while the cycle runs: the whole remaining budget.

        Just today's count when there is no cap (``remaining is None``), where
        nothing is reserved. One spelling for the booking write and the hand-back's
        guard — a second one would silently strand the reservation it cannot match.
        """
        return self.daily_count if self.remaining is None else self.daily_count + self.remaining


async def _reconcile_reservation(
    account_id: str,
    reservation: _Reservation,
    spent: int,
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

    Guarded by the booking's own value, NOT by the generation marker (#10). Both
    predicates the state CAS applies — ``run_id`` matches and the row is not
    ``idle`` — are false for the routine case this hand-back exists to serve: the
    stop path clears ``run_id`` and writes ``idle`` as soon as its ~5s cancel wait
    is up, and a cycle stuck on a slow proxy unwinds after that, so the write was
    refused and the day stayed booked. An operator's Stop then Start reached the
    same dead end from the other side, because Start always mints a fresh
    generation. ``daily_actions`` still being exactly what we booked is the precise
    statement of "nobody else has touched today's count", and it also makes the
    write idempotent — see ``hand_back_warming_reservation``.

    ``Exception`` failures are swallowed — never propagated — because this runs on
    the way out of another exception and must never mask it, but they are always
    logged: a silent reconcile failure is the forfeited day this function exists to
    prevent. A ``CancelledError`` is deliberately NOT swallowed (``except
    Exception``, not ``BaseException``): every caller sits inside the loop's own
    abnormal-exit handler, which logs it and re-raises the original exception, so
    eating it here would only break asyncio's contract — the finalize call site has
    no other backstop.
    """
    daily_count = reservation.daily_count
    try:
        # ``shield``: ``shutdown_warming_runtime`` cancels a second time when its
        # 5s ``gather`` times out, and that second cancel lands on this write's
        # ``to_thread``. An abandoned write leaves the full reservation booked and
        # replaces whatever exception was propagating — a genuine crash would then
        # look like a cancellation to ``_runner`` and never park the account.
        applied = await asyncio.shield(
            hand_back_warming_reservation(
                account_id,
                booked=reservation.booked,
                reconciled=daily_count + spent,
                daily_date=reservation.daily_date,
            ),
        )
        if not applied:
            # The booking is no longer on the row (a newer generation already booked
            # its own, or today's count moved on some other way): whatever is booked
            # there is not ours to release, and nothing else will clear it before the
            # next UTC midnight, so this is the case the operator actually has to
            # see — not a successful hand-back.
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
    reservation: _Reservation,
    spent: int,
) -> None:
    """Hand the reservation back while an exception unwinds ``run_loop_iteration``.

    Never raises: the caller re-raises the original exception unchanged, and a
    failure here must not replace it.
    """
    try:
        # A row that is gone entirely (``remove_account`` proceeds after its 5s
        # cancel wait, so ``delete_account`` can purge it while we are still here)
        # holds no reservation to hand back, and reporting one as stranded would
        # hand the operator an alert about an account that no longer exists — same
        # skip as ``_finalize_after_cycle``'s call site.
        latest = await fetch_warming_state(account_id)
        if latest is not None:
            try:
                await _reconcile_reservation(account_id, reservation, spent)
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
