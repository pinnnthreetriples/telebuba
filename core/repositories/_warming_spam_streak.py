"""The unreadable-@SpamBot streak on ``warming_account_state``.

Its own statement rather than a field of ``upsert_warming_state``: only the cycle's
spam step writes it, and it must count atomically — ``streak + 1`` in SQL, like
``cycles_completed`` — so two writers never lose an increment. Guarded by the loop
generation the same way the shared upsert is, so a retired cycle cannot touch a row a
newer generation (or a stop) already owns.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select, update

from core.db import _get_engine, _warming_account_state


def _record_spam_check_outcome(
    account_id: str,
    *,
    readable: bool,
    expected_run_id: str | None,
) -> int | None:
    table = _warming_account_state
    conditions = [table.c.account_id == account_id, table.c.state != "idle"]
    if expected_run_id is not None:
        conditions.append(table.c.run_id == expected_run_id)
    streak = 0 if readable else table.c.spam_unknown_streak + 1
    with _get_engine().begin() as connection:
        result = connection.execute(
            update(table).where(*conditions).values(spam_unknown_streak=streak),
        )
        if result.rowcount == 0:
            return None
        value = connection.execute(
            select(table.c.spam_unknown_streak).where(table.c.account_id == account_id),
        ).scalar_one()
    return int(value)


async def record_spam_check_outcome(
    account_id: str,
    *,
    readable: bool,
    expected_run_id: str | None,
) -> int | None:
    """Reset the streak on a reading, else add one; ``None`` when the guard refused.

    ``expected_run_id=None`` skips the generation guard (a direct, loop-less call),
    never the ``idle`` one: a stopped account's row is not this writer's to touch.
    """
    return await asyncio.to_thread(
        _record_spam_check_outcome,
        account_id,
        readable=readable,
        expected_run_id=expected_run_id,
    )
