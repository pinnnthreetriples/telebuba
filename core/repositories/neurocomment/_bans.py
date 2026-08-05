"""The readiness table's ban columns: the sticky auto-ban (#30) and the refusal count (#47).

Split from ``_comments.py`` for the file-size budget (mirrors ``_deletions.py``).
A ban is PERMANENT, by product decision — a channel that banned an account is closed
to it for good, and the operator's remedy is another account, not a way back. So there
is deliberately no un-ban here: ``upsert_readiness`` never touches ``banned`` (a
re-onboard cannot revive the pair), and only ``delete_readiness`` — which drops the row
entirely — clears it. The live can_send probe behind "Проверить каналы" used to lift a
ban; it was removed rather than left contradicting what the UI now tells the operator.

The unconfirmed-refusal counter (#47) lives here rather than in ``_readiness.py`` for the
same budget and because it is the same table's ban columns: it is what parks a pair when
Telegram never confirms a per-group ban at all.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import ColumnElement, case, select, update

from core.db import _get_engine, _now_iso
from core.repositories.neurocomment._tables import _neurocomment_readiness


def _mark_pair_banned(account_id: str, channel: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_neurocomment_readiness)
            .where(
                (_neurocomment_readiness.c.account_id == account_id)
                & (_neurocomment_readiness.c.channel == channel),
            )
            .values(banned=1, ready=0, checked_at=_now_iso()),
        )


async def mark_pair_banned(account_id: str, channel: str) -> None:
    """Auto-ban (#30): a UserBannedInChannelError parks this pair (ready=0, banned=1)."""
    await asyncio.to_thread(_mark_pair_banned, account_id, channel)


def _interval_elapsed(interval_start: str) -> ColumnElement[bool]:
    """No counted refusal on record for this pair, or one older than ``interval_start``.

    One expression, two users: the counting UPDATE — the only place the interval can be
    applied without a race — and the read that lets a caller turn a refusal away before
    paying for anything else. They cannot drift because there is only one of them.
    """
    return _neurocomment_readiness.c.unconfirmed_ban_at.is_(None) | (
        _neurocomment_readiness.c.unconfirmed_ban_at < interval_start
    )


def _stamp_unconfirmed_ban(
    account_id: str, channel: str, window_start: str, interval_start: str
) -> int:
    with _get_engine().begin() as connection:
        row = connection.execute(
            update(_neurocomment_readiness)
            .where(
                (_neurocomment_readiness.c.account_id == account_id)
                & (_neurocomment_readiness.c.channel == channel)
                # The minimum interval, in the SAME statement as the increment. As a Python
                # check it was separated from this write by an await, so two refusals whose
                # coroutines interleaved both passed it and both counted — a queue of posts
                # on one channel then took a pair from its first refusal to a permanent ban
                # in seconds, and ran the leave once per count.
                & _interval_elapsed(interval_start),
            )
            .values(
                # Rolling window, resolved in the same statement as the increment: a stamp
                # still inside it continues the count, anything older (or NULL, which
                # compares as NULL and falls to the ELSE) starts a fresh one at 1. Doing it
                # as read-then-write would let two refusals racing on the same pair both
                # read the old value and land as one.
                unconfirmed_bans=case(
                    (
                        _neurocomment_readiness.c.unconfirmed_ban_at >= window_start,
                        _neurocomment_readiness.c.unconfirmed_bans + 1,
                    ),
                    else_=1,
                ),
                unconfirmed_ban_at=_now_iso(),
            )
            # RETURNING rather than a read-back SELECT: the new count is what the caller
            # spends its budget against, and no row matched means nothing was counted.
            .returning(_neurocomment_readiness.c.unconfirmed_bans),
        ).scalar()
    return 0 if row is None else int(row)


async def stamp_unconfirmed_ban(
    account_id: str, channel: str, window_start: str, interval_start: str
) -> int:
    """Count one unconfirmed write refusal for this pair; return the running total.

    Both instants are ISO-8601 UTC and both are the CALLER's rule — the window and the
    minimum interval are policy and stay in ``services.neurocomment.bans``; this only
    applies them, in one statement, so no refusal can be counted twice over. A stamp at or
    after ``window_start`` continues the count and an older one restarts it; a stamp at or
    after ``interval_start`` means this refusal is the same episode as the last counted one
    and NOTHING is written — the stamp does not move either.

    Deliberately NOT part of ``upsert_readiness``, for the reason ``stamp_join_request``
    and ``stamp_rejoin_attempt`` are not: onboarding re-writes the readiness row of a pair
    that is still a group member, and a reset riding along would refill the budget on
    every pass. Returns 0 when nothing was counted — the pair has no readiness row, or the
    interval has not run out — so the caller can spend nothing on either.
    """
    return await asyncio.to_thread(
        _stamp_unconfirmed_ban, account_id, channel, window_start, interval_start
    )


def _unconfirmed_ban_is_countable(account_id: str, channel: str, interval_start: str) -> bool:
    statement = select(_neurocomment_readiness.c.account_id).where(
        (_neurocomment_readiness.c.account_id == account_id)
        & (_neurocomment_readiness.c.channel == channel)
        & _interval_elapsed(interval_start),
    )
    with _get_engine().connect() as connection:
        return connection.execute(statement).first() is not None


async def unconfirmed_ban_is_countable(account_id: str, channel: str, interval_start: str) -> bool:
    """Would a refusal counted right now be charged to this pair? Read-only, and NOT a guard.

    ``stamp_unconfirmed_ban`` applies the same clause inside its own UPDATE, which is the
    check a racing refusal cannot slip past; a read never can be. This exists so a caller
    can turn a refusal away BEFORE paying for what comes after it — the @SpamBot reading in
    particular, which is served from cache only inside its TTL and otherwise opens a real
    dialogue on the post hot path. ``False`` also covers a pair with no readiness row:
    there is nothing to count against, which is what the stamp's 0 says too.
    """
    return await asyncio.to_thread(
        _unconfirmed_ban_is_countable, account_id, channel, interval_start
    )


def _clear_unconfirmed_bans(account_id: str, channel: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_neurocomment_readiness)
            .where(
                # Only rows actually carrying a count — this runs on every delivered
                # comment, and the overwhelming majority have nothing to clear.
                (_neurocomment_readiness.c.account_id == account_id)
                & (_neurocomment_readiness.c.channel == channel)
                & (_neurocomment_readiness.c.unconfirmed_bans != 0),
            )
            .values(unconfirmed_bans=0, unconfirmed_ban_at=None),
        )


async def clear_unconfirmed_bans(account_id: str, channel: str) -> None:
    """A comment landed: this pair can write here after all, so the count goes back to 0."""
    await asyncio.to_thread(_clear_unconfirmed_bans, account_id, channel)
