"""Outreach rests — the last limit each account met in outreach, and until when it rests.

One row per account. A new limit replaces the reason and time but never shortens a
rest already in force, so a short flood cannot cut a long rest.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core._schema_outreach import _outreach_account_rests
from core.db import _get_engine
from schemas.outreach_protection import OutreachRest

if TYPE_CHECKING:
    from collections.abc import Sequence


def _record_outreach_rest(rest: OutreachRest) -> None:
    table = _outreach_account_rests
    statement = sqlite_insert(table).values(**rest.model_dump())
    excluded = statement.excluded
    statement = statement.on_conflict_do_update(
        index_elements=[table.c.account_id],
        set_={
            "reason": excluded.reason,
            "limited_at": excluded.limited_at,
            "rest_until": func.max(table.c.rest_until, excluded.rest_until),
        },
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def record_outreach_rest(rest: OutreachRest) -> None:
    """Store an account's latest limit; its rest only ever grows."""
    await asyncio.to_thread(_record_outreach_rest, rest)


def _list_outreach_rests(account_ids: Sequence[str]) -> dict[str, OutreachRest]:
    table = _outreach_account_rests
    statement = select(table).where(table.c.account_id.in_(account_ids))
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return {str(row["account_id"]): OutreachRest.model_validate(dict(row)) for row in rows}


async def list_outreach_rests(account_ids: Sequence[str]) -> dict[str, OutreachRest]:
    """The stored rest of each listed account that ever met an outreach limit."""
    return await asyncio.to_thread(_list_outreach_rests, list(account_ids))
