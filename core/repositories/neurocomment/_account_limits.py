"""Per-account cap overrides, and the rolling windows the operator reads them against.

Two jobs in one module because they are one screen: the stored override row (migration #58) and
the "how much of it is spent, and when does a slot come back" reads that give the number
meaning. Splitting them would leave two modules of forty lines that are never touched
apart.

The window reads answer a question the counters in ``_quota`` / ``_joins`` cannot: those
return how MANY rows sit inside the window, which is all a gate needs, while a gauge also
has to say WHEN the window next gives a slot back. Which row that is depends on the cap, so
the cap goes in as an argument and the count and the stamp are read inside ONE transaction:
choosing the row from a count read a moment earlier let a join landing in between name a
row hours too early, which is the very error this read exists to avoid.

Public functions wrap sync helpers via ``asyncio.to_thread`` and return Pydantic models,
never raw rows — the repository boundary ``tests/test_architecture.py`` enforces.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete, func, insert, select

from core.db import _accounts, _get_engine, _now_iso
from core.repositories.neurocomment._quota import _QUOTA_SPENDING_STATUSES
from core.repositories.neurocomment._tables import (
    _neurocomment_account_limits,
    _neurocomment_comments,
    _neurocomment_join_log,
)
from schemas.neurocomment_limits import AccountLimitOverride, AccountLimitsUpdate, LimitWindow

if TYPE_CHECKING:
    from collections.abc import Mapping

_OVERRIDE_COLUMNS = (
    "max_joins_per_day",
    "max_comments_per_hour",
    "max_comments_per_channel_per_day",
)


def _row_to_override(account_id: str, mapping: Mapping[str, Any] | None) -> AccountLimitOverride:
    if mapping is None:
        return AccountLimitOverride(account_id=account_id)
    values = {
        column: None if mapping[column] is None else int(cast("int", mapping[column]))
        for column in _OVERRIDE_COLUMNS
    }
    return AccountLimitOverride(account_id=account_id, **values)


def _load_account_limit_overrides(account_ids: list[str]) -> dict[str, AccountLimitOverride]:
    if not account_ids:
        return {}
    statement = select(_neurocomment_account_limits).where(
        _neurocomment_account_limits.c.account_id.in_(account_ids),
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return {
        str(row["account_id"]): _row_to_override(
            str(row["account_id"]),
            cast("Mapping[str, Any]", row),
        )
        for row in rows
    }


async def load_account_limit_overrides(
    account_ids: list[str],
) -> dict[str, AccountLimitOverride]:
    """The stored overrides of the given accounts, keyed by id — absent = no override.

    Scoped to ``account_ids`` like every other bulk selection read, so the cost stays
    O(candidates) rather than O(fleet) on a table that only grows as operators tune it.
    """
    return await asyncio.to_thread(_load_account_limit_overrides, account_ids)


async def load_account_limit_override(account_id: str) -> AccountLimitOverride:
    """One account's override row, or an all-``None`` one when it has never been tuned."""
    overrides = await load_account_limit_overrides([account_id])
    return overrides.get(account_id, AccountLimitOverride(account_id=account_id))


def _save_account_limit_override(
    account_id: str,
    data: AccountLimitsUpdate,
) -> AccountLimitOverride | None:
    values = {column: getattr(data, column) for column in _OVERRIDE_COLUMNS}
    with _get_engine().begin() as connection:
        # The account is checked HERE, inside the write transaction, rather than by the
        # caller beforehand: the column has no foreign key, so a delete landing between a
        # caller's check and this insert would leave exactly the orphan row the check
        # exists to prevent -- and nothing collects it.
        exists = connection.execute(
            select(func.count()).where(_accounts.c.account_id == account_id),
        ).scalar_one()
        if not exists:
            return None
        # Delete-then-insert rather than an upsert: an edit that clears every cap leaves
        # nothing to store, and a row of three NULLs would be a permanent record that the
        # account is "tuned" when it follows the fleet exactly like an untouched one.
        connection.execute(
            delete(_neurocomment_account_limits).where(
                _neurocomment_account_limits.c.account_id == account_id,
            ),
        )
        if any(value is not None for value in values.values()):
            connection.execute(
                insert(_neurocomment_account_limits).values(
                    account_id=account_id,
                    updated_at=_now_iso(),
                    **values,
                ),
            )
    return AccountLimitOverride(account_id=account_id, **values)


async def save_account_limit_override(
    account_id: str,
    data: AccountLimitsUpdate,
) -> AccountLimitOverride | None:
    """Replace one account's override row, or ``None`` if there is no such account.

    All-``None`` values remove the row entirely -- back to following the fleet.
    """
    return await asyncio.to_thread(_save_account_limit_override, account_id, data)


def _slot_offset(used: int, cap: int) -> int:
    """Index (oldest first) of the row whose ageing-out puts the window back under ``cap``.

    ``0`` while the window is at or under the cap -- the oldest row frees the next slot.
    Over the cap, ``used - cap`` rows have to leave before the account is allowed through
    again, and this is the last of them. ``cap == 0`` is "no cap", which nothing waits on.
    """
    return used - cap if 0 < cap < used else 0


def _join_window(account_id: str, since_iso: str, cap: int) -> LimitWindow:
    where = (_neurocomment_join_log.c.account_id == account_id) & (
        _neurocomment_join_log.c.joined_at >= since_iso
    )
    with _get_engine().begin() as connection:
        used = int(connection.execute(select(func.count()).where(where)).scalar_one())
        slot = connection.execute(
            select(_neurocomment_join_log.c.joined_at)
            .where(where)
            .order_by(_neurocomment_join_log.c.joined_at)
            .offset(_slot_offset(used, cap))
            .limit(1),
        ).scalar()
    return LimitWindow(used=used, slot_at=None if slot is None else str(slot))


async def account_join_window(account_id: str, since_iso: str, cap: int) -> LimitWindow:
    """Joins inside the window, and the stamp that frees a slot at ``cap``.

    The count mirrors the join cap's own byte for byte, lost joins included: the RPC was
    spent whether or not the membership still stands.
    """
    return await asyncio.to_thread(_join_window, account_id, since_iso, cap)


def _comment_window(account_id: str, since_iso: str, cap: int) -> LimitWindow:
    where = (
        (_neurocomment_comments.c.account_id == account_id)
        & (_neurocomment_comments.c.status.in_(_QUOTA_SPENDING_STATUSES))
        & (_neurocomment_comments.c.created_at >= since_iso)
    )
    with _get_engine().begin() as connection:
        used = int(connection.execute(select(func.count()).where(where)).scalar_one())
        slot = connection.execute(
            select(_neurocomment_comments.c.created_at)
            .where(where)
            .order_by(_neurocomment_comments.c.created_at)
            .offset(_slot_offset(used, cap))
            .limit(1),
        ).scalar()
    return LimitWindow(used=used, slot_at=None if slot is None else str(slot))


async def account_comment_window(account_id: str, since_iso: str, cap: int) -> LimitWindow:
    """Quota-spending comments in the window, and the stamp that frees a slot at ``cap``."""
    return await asyncio.to_thread(_comment_window, account_id, since_iso, cap)


def _busiest_channel_window(account_id: str, since_iso: str, cap: int) -> LimitWindow:
    spent = (
        (_neurocomment_comments.c.account_id == account_id)
        & (_neurocomment_comments.c.status.in_(_QUOTA_SPENDING_STATUSES))
        & (_neurocomment_comments.c.created_at >= since_iso)
    )
    busiest = (
        select(_neurocomment_comments.c.channel, func.count().label("n"))
        .where(spent)
        .group_by(_neurocomment_comments.c.channel)
        # Handle after count, so a tie resolves the same way twice running rather than
        # letting the operator's gauge flip between two channels on a refresh.
        .order_by(func.count().desc(), _neurocomment_comments.c.channel)
        .limit(1)
    )
    with _get_engine().begin() as connection:
        row = connection.execute(busiest).first()
        if row is None:
            return LimitWindow(used=0)
        channel = str(row[0])
        used = int(row[1])
        slot = connection.execute(
            select(_neurocomment_comments.c.created_at)
            .where(spent & (_neurocomment_comments.c.channel == channel))
            .order_by(_neurocomment_comments.c.created_at)
            .offset(_slot_offset(used, cap))
            .limit(1),
        ).scalar()
    return LimitWindow(
        used=used,
        slot_at=None if slot is None else str(slot),
        channel=channel,
    )


async def account_busiest_channel_window(
    account_id: str,
    since_iso: str,
    cap: int,
) -> LimitWindow:
    """The channel this account has spent the most of the per-pair day cap on.

    The cap is per (account, channel), so a single account-wide number would answer a
    question nobody asked. The channel closest to its own cap is the one that decides
    whether the account can still comment where it matters, so that is the pair the
    gauge reports -- named, so the number can be checked.
    """
    return await asyncio.to_thread(_busiest_channel_window, account_id, since_iso, cap)
