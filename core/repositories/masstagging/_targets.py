"""Target rows, their history, and the registry of everyone ever tagged.

Every write takes ``at`` from the engine's clock, so the board's "when" and the
"stop after N" window are on one clock. A state move is a compare-and-set on the states
a target may be leaving, so a manual action from the board is never overwritten by a
worker that read the row a moment earlier.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.db import _get_engine
from core.repositories.masstagging._tables import (
    _masstagging_journal,
    _masstagging_tagged_registry,
    _masstagging_targets,
)
from schemas.masstagging_records import JournalRecord, TargetRecord, TargetSeed

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy import Connection, RowMapping

_TARGETS = _masstagging_targets
_JOURNAL = _masstagging_journal
# Rows the run itself added (a chat's members, the accounts' contacts): a list saved
# while the campaign is paused does not name them, so a merge must not drop them.
_RUN_KINDS = ("link", "contact")


def _insert_seeds(
    connection: Connection, campaign_id: str, seeds: list[TargetSeed], start: int, at: int
) -> None:
    if seeds:
        connection.execute(
            insert(_TARGETS),
            [
                {
                    "campaign_id": campaign_id,
                    "position": start + offset,
                    "updated_unix": at,
                    **seed.model_dump(),
                }
                for offset, seed in enumerate(seeds)
            ],
        )


def _replace_targets(campaign_id: str, seeds: list[TargetSeed], at: int) -> None:
    with _get_engine().begin() as connection:
        connection.execute(delete(_TARGETS).where(_TARGETS.c.campaign_id == campaign_id))
        connection.execute(delete(_JOURNAL).where(_JOURNAL.c.campaign_id == campaign_id))
        _insert_seeds(connection, campaign_id, seeds, 0, at)


async def replace_targets(campaign_id: str, seeds: list[TargetSeed], *, at: int) -> None:
    """A fresh run's targets, in order; the previous run's rows and history go."""
    await asyncio.to_thread(_replace_targets, campaign_id, seeds, at)


def _existing_keys(connection: Connection, campaign_id: str) -> set[str]:
    rows = connection.execute(
        select(_TARGETS.c.target_key).where(_TARGETS.c.campaign_id == campaign_id)
    )
    return {str(key) for key in rows.scalars()}


def _next_position(connection: Connection, campaign_id: str) -> int:
    top = connection.execute(
        select(func.max(_TARGETS.c.position)).where(_TARGETS.c.campaign_id == campaign_id)
    ).scalar_one_or_none()
    return 0 if top is None else int(top) + 1


def _add_targets(campaign_id: str, seeds: list[TargetSeed], at: int) -> int:
    with _get_engine().begin() as connection:
        existing = _existing_keys(connection, campaign_id)
        fresh = list({s.target_key: s for s in seeds if s.target_key not in existing}.values())
        _insert_seeds(connection, campaign_id, fresh, _next_position(connection, campaign_id), at)
    return len(fresh)


async def add_targets(campaign_id: str, seeds: list[TargetSeed], *, at: int) -> int:
    """Append targets the campaign does not have yet; returns how many were new."""
    return await asyncio.to_thread(_add_targets, campaign_id, seeds, at)


def _merge_targets(campaign_id: str, seeds: list[TargetSeed], at: int) -> None:
    keys = {seed.target_key for seed in seeds}
    with _get_engine().begin() as connection:
        connection.execute(
            delete(_TARGETS).where(
                _TARGETS.c.campaign_id == campaign_id,
                _TARGETS.c.state == "queued",
                _TARGETS.c.kind.not_in(_RUN_KINDS),
                _TARGETS.c.target_key.not_in(keys),
            ),
        )
        existing = _existing_keys(connection, campaign_id)
        fresh = [seed for seed in seeds if seed.target_key not in existing]
        _insert_seeds(connection, campaign_id, fresh, _next_position(connection, campaign_id), at)


async def merge_targets(campaign_id: str, seeds: list[TargetSeed], *, at: int) -> None:
    """A continued run takes the list as saved now: new names join, untouched removed go.

    Whatever a target already went through stays, and so do the rows the run added
    itself (a chat's members, the contacts).
    """
    await asyncio.to_thread(_merge_targets, campaign_id, seeds, at)


def _target(row: RowMapping) -> TargetRecord:
    return TargetRecord.model_validate(dict(row))


def _list_targets(campaign_id: str) -> list[TargetRecord]:
    statement = (
        select(_TARGETS)
        .where(_TARGETS.c.campaign_id == campaign_id)
        .order_by(_TARGETS.c.position.asc())
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [_target(row) for row in rows]


async def list_targets(campaign_id: str) -> list[TargetRecord]:
    return await asyncio.to_thread(_list_targets, campaign_id)


def _fetch_target(campaign_id: str, target_key: str) -> TargetRecord | None:
    statement = select(_TARGETS).where(
        _TARGETS.c.campaign_id == campaign_id, _TARGETS.c.target_key == target_key
    )
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    return None if row is None else _target(row)


async def fetch_target(campaign_id: str, target_key: str) -> TargetRecord | None:
    return await asyncio.to_thread(_fetch_target, campaign_id, target_key)


def _update_targets(
    campaign_id: str,
    keys: list[str],
    expect_states: tuple[str, ...] | None,
    fields: dict[str, object],
) -> list[str]:
    statement = update(_TARGETS).where(
        _TARGETS.c.campaign_id == campaign_id, _TARGETS.c.target_key.in_(keys)
    )
    if expect_states is not None:
        statement = statement.where(_TARGETS.c.state.in_(expect_states))
    with _get_engine().begin() as connection:
        moved = connection.execute(statement.values(**fields).returning(_TARGETS.c.target_key))
        return [str(key) for key in moved.scalars()]


async def update_targets(
    campaign_id: str,
    keys: list[str],
    *,
    at: int,
    expect_states: Iterable[str] | None = None,
    **fields: object,
) -> list[str]:
    """Write the same fields to several targets; returns the keys actually written.

    With ``expect_states``, only the targets still in one of them are written.
    """
    if not keys:
        return []
    states = None if expect_states is None else tuple(expect_states)
    values = {"updated_unix": at, **fields}
    return await asyncio.to_thread(_update_targets, campaign_id, keys, states, values)


async def update_target(
    campaign_id: str,
    target_key: str,
    *,
    at: int,
    expect_states: Iterable[str] | None = None,
    **fields: object,
) -> bool:
    """Write one target's fields; with ``expect_states``, only while it is in one of them."""
    moved = await update_targets(
        campaign_id, [target_key], at=at, expect_states=expect_states, **fields
    )
    return bool(moved)


def _count_tagged_since(campaign_id: str, account_id: str, since: int) -> int:
    statement = select(func.count()).where(
        _TARGETS.c.campaign_id == campaign_id,
        _TARGETS.c.account_id == account_id,
        _TARGETS.c.state == "tagged",
        _TARGETS.c.updated_unix >= since,
    )
    with _get_engine().connect() as connection:
        return int(connection.execute(statement).scalar_one())


async def count_tagged_since(campaign_id: str, account_id: str, since: int) -> int:
    """People this account tagged in this campaign since ``since`` ("stop after N")."""
    return await asyncio.to_thread(_count_tagged_since, campaign_id, account_id, since)


def _add_events(campaign_id: str, events: list[JournalRecord]) -> None:
    if not events:
        return
    with _get_engine().begin() as connection:
        connection.execute(
            insert(_JOURNAL),
            [{"campaign_id": campaign_id, **event.model_dump()} for event in events],
        )


async def add_events(campaign_id: str, events: list[JournalRecord]) -> None:
    await asyncio.to_thread(_add_events, campaign_id, events)


def _list_journal(campaign_id: str) -> list[JournalRecord]:
    statement = (
        select(
            _JOURNAL.c.target_key,
            _JOURNAL.c.event,
            _JOURNAL.c.at_unix,
            _JOURNAL.c.account_id,
            _JOURNAL.c.detail,
        )
        .where(_JOURNAL.c.campaign_id == campaign_id)
        .order_by(_JOURNAL.c.at_unix.asc(), _JOURNAL.c.id.asc())
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [JournalRecord.model_validate(dict(row)) for row in rows]


async def list_journal(campaign_id: str) -> list[JournalRecord]:
    """Every target's history in this run, oldest first."""
    return await asyncio.to_thread(_list_journal, campaign_id)


def _tagged_user_ids(user_ids: list[int]) -> set[int]:
    registry = _masstagging_tagged_registry
    statement = select(registry.c.user_id).where(registry.c.user_id.in_(user_ids))
    with _get_engine().connect() as connection:
        return {int(value) for value in connection.execute(statement).scalars()}


async def tagged_user_ids(user_ids: list[int]) -> set[int]:
    """Which of these people any campaign has already tagged."""
    if not user_ids:
        return set()
    return await asyncio.to_thread(_tagged_user_ids, user_ids)


def _register_tagged(user_ids: list[int], campaign_id: str, at: int) -> None:
    registry = _masstagging_tagged_registry
    with _get_engine().begin() as connection:
        connection.execute(
            sqlite_insert(registry).on_conflict_do_nothing(index_elements=["user_id"]),
            [
                {"user_id": user_id, "campaign_id": campaign_id, "at_unix": at}
                for user_id in user_ids
            ],
        )


async def register_tagged(user_ids: list[int], *, campaign_id: str, at: int) -> None:
    """Remember these people as tagged; the first campaign that did it keeps the row."""
    if user_ids:
        await asyncio.to_thread(_register_tagged, user_ids, campaign_id, at)
