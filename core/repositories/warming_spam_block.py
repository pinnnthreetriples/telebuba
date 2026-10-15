"""The one write that lifts a confirmed spam block from ``warming_account_state``.

Its own statement, outside the main upsert, because the main upsert never clears
``spam_blocked_at`` (``None`` there means "leave the column alone"). The UPDATE is a
compare-and-set on the mark itself: it applies only while the row still carries the
mark the caller read, so a block confirmed again in between (a new, later mark) is
never lifted by an answer that predates it, and two lifts racing on the same answer
apply once. Every other column a cycle or the operator writes is left alone, except
for the parked ``spam_blocked`` row, which goes idle in the same transaction: the
stop already ended its wave, and no loop runs for it.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import update

from core.db import _get_engine, _now_iso, _warming_account_state

_PARKED = "spam_blocked"


def _lift_spam_block_mark(account_id: str, mark: str) -> str | None:
    table = _warming_account_state
    marked = (table.c.account_id == account_id) & (table.c.spam_blocked_at == mark)
    now = _now_iso()
    # Two statements, each a compare-and-set on the mark: the parked row goes idle with
    # the lift, any other row only loses the mark. Whichever applies says which it was.
    parked = (
        update(table)
        .where(marked, table.c.state == _PARKED)
        .values(
            spam_blocked_at=None,
            state="idle",
            last_event="spam_block_lifted",
            last_error=None,
            run_id=None,
            next_run_at=None,
            updated_at=now,
        )
        .returning(table.c.state)
    )
    other = (
        update(table)
        .where(marked, table.c.state != _PARKED)
        .values(spam_blocked_at=None, updated_at=now)
        .returning(table.c.state)
    )
    with _get_engine().begin() as connection:
        if connection.execute(parked).first() is not None:
            return _PARKED
        row = connection.execute(other).first()
    return None if row is None else str(row[0])


async def lift_spam_block_mark(account_id: str, mark: str) -> str | None:
    """Clear ``spam_blocked_at`` while it still equals ``mark``; a parked row goes idle.

    Returns the state the row was in (``spam_blocked`` for a parked row that went idle),
    or ``None`` when the mark had already changed and nothing was written.
    """
    return await asyncio.to_thread(_lift_spam_block_mark, account_id, mark)
