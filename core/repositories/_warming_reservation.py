"""The daily-budget reservation hand-back write — a sibling of ``core.repositories.warming``.

Its own module for the file-size budget — that one was at the cap when this write was
written, and splitting it was cheaper than trimming prose — like
``core.repositories._warming_settings``; re-exported there, and thence by ``core.db``,
so call sites keep importing it from either. Owns ONE statement: the guarded swap of a
pre-cycle daily reservation down to what the cycle really spent (#208, #10).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from sqlalchemy import select, update

from core.db import _get_engine, _now_iso, _warming_account_state

if TYPE_CHECKING:
    from collections.abc import Mapping

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

    A refusal is classified from the row itself, because only two of the four ways to be
    refused actually cost the account budget — see :func:`_classify_refusal`.
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
    row_stmt = select(
        _warming_account_state.c.reservation_token,
        _warming_account_state.c.daily_actions,
        _warming_account_state.c.daily_count_date,
    ).where(_warming_account_state.c.account_id == account_id)
    with _get_engine().begin() as connection:
        if connection.execute(update_stmt).rowcount > 0:
            return "applied"
        row = connection.execute(row_stmt).mappings().first()
    if row is None:
        # ``remove_account`` purged it; there is no budget left to owe anyone.
        return "settled"
    return _classify_refusal(
        cast("Mapping[str, object]", row), token=token, booked=booked, daily_date=daily_date
    )


def _classify_refusal(
    row: Mapping[str, object],
    *,
    token: str,
    booked: int,
    daily_date: str,
) -> WarmingHandBack:
    """Say whether a refused hand-back cost the account the rest of its day.

    The row's three facts — whose booking is on it, for which date, and how big the
    count is — partition the refusals exhaustively, and only two leaves are losses:

    * ``settled``, token NULL. Three writers put NULL in this column, by enumeration:
      this hand-back, the INSERT branch of ``_upsert_warming_state`` (which omits the
      column, so a brand-new row starts NULL), and migration #46 backfilling every row
      that predates it. Only the first can co-occur with a live booking — a row created
      or migrated without one cannot carry the uuid4 we minted — so on a booking's own
      hand-back, NULL means OUR earlier write landed: the shielded one whose result the
      cancel swallowed. (A never-booked NULL row also lands here, which is correct: it
      owes nobody anything.)
    * ``absorbed``, someone else's token. A newer booking replaced ours, and it read its
      baseline off a row that still carried our booking, so our unspent remainder is
      now inside its count and no longer available to anyone. A phase advance raising
      the cap is the reachable way in: the daily gate parks a generation that finds a
      saturated count under the SAME cap, but admits it under a higher one.
    * ``settled``, our token but the date has rolled — checked BEFORE the count, because
      a rolled date makes the count a different day's and comparing it would be
      meaningless — or, on our own date, a count strictly below what we booked. Both
      mean the budget is already free: a fresh day resets the counter, and our own
      ``_finalize_after_cycle`` transition writes exactly the reconciled count without
      clearing the token, so a cancel landing after it arrives here.
    * ``stranded``, our token, our date, and a count at or above our booking. ``>=``,
      not ``>``: equality cannot actually arrive here (the UPDATE's own predicate would
      have matched it and returned ``applied``, and SQLite's single writer means nothing
      slips in between the two statements of this transaction), so the boundary is
      defensive and points at fail-loud.
      Our reservation is still being counted with nobody left to release it. No code
      path is known to produce it — a newer generation either parks (writing the count
      we booked, which the guard above then matches) or books (taking the token, i.e.
      ``absorbed``) — so it stays as the fail-loud branch for an accounting movement
      nobody has explained, rather than being assumed away.
    """
    if row["reservation_token"] != token:
        return "settled" if row["reservation_token"] is None else "absorbed"
    if row["daily_count_date"] != daily_date:
        return "settled"
    return "stranded" if cast("int", row["daily_actions"] or 0) >= booked else "settled"


async def hand_back_warming_reservation(
    account_id: str,
    *,
    token: str,
    booked: int,
    reconciled: int,
    daily_date: str,
) -> WarmingHandBack:
    """Apply the reservation hand-back and say which of the four outcomes it was."""
    return await asyncio.to_thread(
        _hand_back_reservation, account_id, token, booked, reconciled, daily_date
    )
