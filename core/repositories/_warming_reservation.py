"""The daily-budget reservation hand-back write — a sibling of ``core.repositories.warming``.

Its own module for the file-size budget (that one is at the cap), like
``core.repositories._warming_settings``; re-exported there, and thence by ``core.db``,
so call sites keep importing it from either. Owns ONE statement: the guarded swap of a
pre-cycle daily reservation down to what the cycle really spent (#208, #10).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from core.db import _get_engine, _now_iso, _warming_account_state

if TYPE_CHECKING:
    from schemas.warming import WarmingHandBack


def _hand_back_reservation(
    account_id: str,
    token: str,
    booked: int,
    reconciled: int,
    daily_date: str,
) -> WarmingHandBack:
    """Swap the pre-cycle daily reservation down to the real spend (#208, #10).

    Deliberately NOT an ``upsert_warming_state`` call: this write owns ONE column and
    the generation CAS there cannot express its guard. The booking is identified by
    ``reservation_token`` — minted per booking by the ``cycle_started`` write — so the
    hand-back lands on a row the operator has already stopped (``run_id`` cleared,
    ``state`` idle) or one a fresh ``start_warming`` generation has taken over but not
    yet booked against, and is refused against a live re-booking EVEN WHEN the booked
    number is identical, which it usually is (it saturates to the phase cap). Applying
    clears the token, so the retry the loop's exit handler makes after a cancelled,
    shielded write is refused by construction. Update-only: a purged row matches
    nothing.

    The refusal is classified, not merely reported: a token that is gone or belongs to
    someone else means this booking was already settled — by our own earlier write, or
    by a newer booking, which can only exist if the count had already been reconciled
    (the daily gate parks a generation that finds a full booking instead of letting it
    re-book). Only a token that is still OURS, with the count or the date moved under
    it, is a reservation left booked with nobody to release it.
    """
    update_stmt = (
        update(_warming_account_state)
        .where(
            (_warming_account_state.c.account_id == account_id)
            & (_warming_account_state.c.reservation_token == token)
            & (_warming_account_state.c.daily_count_date == daily_date)
            & (_warming_account_state.c.daily_actions == booked),
        )
        .values(daily_actions=reconciled, reservation_token=None, updated_at=_now_iso())
    )
    token_stmt = select(_warming_account_state.c.reservation_token).where(
        _warming_account_state.c.account_id == account_id,
    )
    with _get_engine().begin() as connection:
        if connection.execute(update_stmt).rowcount > 0:
            return "applied"
        row = connection.execute(token_stmt).first()
    return "stranded" if row is not None and row[0] == token else "superseded"


async def hand_back_warming_reservation(
    account_id: str,
    *,
    token: str,
    booked: int,
    reconciled: int,
    daily_date: str,
) -> WarmingHandBack:
    """Apply the reservation hand-back and say which of the three outcomes it was."""
    return await asyncio.to_thread(
        _hand_back_reservation, account_id, token, booked, reconciled, daily_date
    )
