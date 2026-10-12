"""The once-per-day worked-day write — a sibling of ``core.repositories.warming``.

Its own module for the file-size budget, like ``_warming_reservation``. Owns ONE
statement: add a day to ``warmed_days`` the first time a local date earns it.
``core.repositories.warming`` and ``core.db`` re-export it.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import or_, update

from core.db import _get_engine, _now_iso, _warming_account_state

# A worked cycle settles the row in ``sleeping`` and a quiet day parks it there; a
# row in any other state (stopped, error, quarantine, flood) earns nothing.
_COUNTABLE_STATES = ("active", "sleeping")


def _count_warmed_day(account_id: str, local_date: str, expected_run_id: str | None) -> int | None:
    """Count ``local_date`` once; return the new total, or ``None`` when not counted.

    Not an ``upsert_warming_state`` call: the guard must read the stored date in
    the same statement, so two writers on one day cannot both count it. ISO dates
    compare as strings, so a date at or before the last counted one is refused —
    which also absorbs a legacy day stored one day ahead. ``expected_run_id`` fences
    out a retired generation, as the state CAS does.
    """
    table = _warming_account_state
    condition = (
        (table.c.account_id == account_id)
        & table.c.state.in_(_COUNTABLE_STATES)
        & or_(table.c.last_warmed_date.is_(None), table.c.last_warmed_date < local_date)
    )
    if expected_run_id is not None:
        condition &= table.c.run_id == expected_run_id
    stmt = (
        update(table)
        .where(condition)
        .values(
            warmed_days=table.c.warmed_days + 1,
            last_warmed_date=local_date,
            updated_at=_now_iso(),
        )
        .returning(table.c.warmed_days)
    )
    with _get_engine().begin() as connection:
        row = connection.execute(stmt).first()
    return None if row is None else int(row[0])


async def count_warmed_day(
    account_id: str,
    local_date: str,
    *,
    expected_run_id: str | None = None,
) -> int | None:
    """Add ``local_date`` to the account's worked days at most once."""
    return await asyncio.to_thread(_count_warmed_day, account_id, local_date, expected_run_id)
