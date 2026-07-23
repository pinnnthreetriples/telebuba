"""Durable engine-cooldown deadlines (restart survival, migration #34).

The engine parks an account after a FloodWait/peer-flood (account-wide) or a
slow-mode wait (per-channel); those deadlines live in-memory in
``services.neurocomment._state`` for the hot read path. This module is their
durable backing so a process restart no longer makes a just-flooded account
eligible to comment again. In-memory stays authoritative for reads — the DB is
written on each ``set_cooldown`` and reloaded once at startup.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.db import _get_engine
from core.repositories.neurocomment._tables import _neurocomment_cooldowns
from schemas.neurocomment import CooldownDeadline

if TYPE_CHECKING:
    from collections.abc import Mapping


def persist_cooldown(account_id: str, channel: str | None, until: str) -> None:
    """Upsert one cooldown deadline (``until`` is an ISO-8601 UTC string).

    Synchronous SQLite write; callers run it off the event loop via
    ``asyncio.to_thread`` (``set_cooldown``) so the single-worker loop never
    blocks on disk I/O during a flood storm. ``channel=None`` (account-wide) is
    stored as ``''`` so it shares the primary key without SQLite's
    NULL-is-distinct upsert hole.
    """
    statement = (
        sqlite_insert(_neurocomment_cooldowns)
        .values(account_id=account_id, channel=channel or "", until=until)
        .on_conflict_do_update(
            index_elements=[
                _neurocomment_cooldowns.c.account_id,
                _neurocomment_cooldowns.c.channel,
            ],
            set_={"until": until},
        )
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)


def _row_to_cooldown(mapping: Mapping[str, object]) -> CooldownDeadline:
    channel = str(mapping["channel"])
    return CooldownDeadline(
        account_id=str(mapping["account_id"]),
        channel=channel or None,
        until=cast("str", mapping["until"]),
    )


def _load_active_cooldowns(now: str) -> list[CooldownDeadline]:
    with _get_engine().begin() as connection:
        # Prune every lapsed deadline (including rows orphaned by a deleted
        # account) so the table never grows without bound, then return only what
        # is still in force for the caller to rehydrate.
        connection.execute(
            delete(_neurocomment_cooldowns).where(_neurocomment_cooldowns.c.until <= now),
        )
        rows = (
            connection.execute(
                select(_neurocomment_cooldowns).where(_neurocomment_cooldowns.c.until > now),
            )
            .mappings()
            .all()
        )
    return [_row_to_cooldown(cast("Mapping[str, object]", row)) for row in rows]


async def load_active_cooldowns(now: str) -> list[CooldownDeadline]:
    """Prune lapsed rows and return the cooldowns still in force (startup hydrate)."""
    return await asyncio.to_thread(_load_active_cooldowns, now)
