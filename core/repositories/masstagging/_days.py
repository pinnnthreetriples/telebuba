"""Per-account day counters: stories, dialogs, tags, lookups, floods in a row.

Telegram counts its limits per account whatever campaign spends them, so the counters
are keyed by account and day, not by campaign. A day is a string the service computes
(it starts at 09:00 local), so a new day is simply a row nobody has written yet.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.db import _get_engine
from core.repositories.masstagging._tables import _masstagging_account_day
from schemas.masstagging_records import AccountDay

_DAY = _masstagging_account_day
_COUNTERS = frozenset({"stories", "dialogs", "tags", "resolves"})
_FIELDS = frozenset({"floods_in_row", "paused_until_unix"})


def _fetch_days(account_ids: list[str], day: str) -> dict[str, AccountDay]:
    statement = select(_DAY).where(_DAY.c.account_id.in_(account_ids), _DAY.c.day == day)
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    found = {str(row["account_id"]): AccountDay.model_validate(dict(row)) for row in rows}
    return {
        account_id: found.get(account_id, AccountDay(account_id=account_id, day=day))
        for account_id in account_ids
    }


async def fetch_days(account_ids: list[str], day: str) -> dict[str, AccountDay]:
    """Each account's counters for ``day``; an account with no row yet reads all zero."""
    return await asyncio.to_thread(_fetch_days, account_ids, day)


def _bump_day(account_id: str, day: str, increments: dict[str, int]) -> None:
    values = {"account_id": account_id, "day": day, **increments}
    statement = sqlite_insert(_DAY).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=["account_id", "day"],
        set_={name: _DAY.c[name] + amount for name, amount in increments.items()},
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def bump_day(account_id: str, day: str, **increments: int) -> None:
    """Add to this account's counters for ``day``."""
    unknown = set(increments) - _COUNTERS
    if unknown or not increments:
        msg = f"Not a day counter: {sorted(unknown)}"
        raise ValueError(msg)
    await asyncio.to_thread(_bump_day, account_id, day, increments)


def _set_day(account_id: str, day: str, fields: dict[str, object]) -> None:
    statement = sqlite_insert(_DAY).values(account_id=account_id, day=day, **fields)
    statement = statement.on_conflict_do_update(index_elements=["account_id", "day"], set_=fields)
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def set_day(account_id: str, day: str, **fields: object) -> None:
    """Overwrite the flood streak or the flood pause of this account's ``day``."""
    unknown = set(fields) - _FIELDS
    if unknown or not fields:
        msg = f"Not a day field: {sorted(unknown)}"
        raise ValueError(msg)
    await asyncio.to_thread(_set_day, account_id, day, fields)


def _first_days(account_ids: list[str]) -> dict[str, str]:
    statement = (
        select(_DAY.c.account_id, func.min(_DAY.c.day))
        .where(_DAY.c.account_id.in_(account_ids))
        .group_by(_DAY.c.account_id)
    )
    with _get_engine().connect() as connection:
        return {str(row[0]): str(row[1]) for row in connection.execute(statement).all()}


async def first_days(account_ids: list[str]) -> dict[str, str]:
    """The first masstagging day of each account that has one (the soft start)."""
    if not account_ids:
        return {}
    return await asyncio.to_thread(_first_days, account_ids)
