"""Roster and chat rows: who serves which chat, and where each chat stands.

Every state write that can race a manual board action is a compare-and-set on the
state the writer last read (``expect_states``), so "skip this chat" from the board is
never overwritten by a worker that read the row a moment earlier.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from sqlalchemy import delete, insert, select, update

from core.db import _get_engine
from core.repositories.chat_broadcast._tables import (
    _chat_broadcast_accounts,
    _chat_broadcast_targets,
)
from schemas.chat_broadcast_records import RosterAccount, TargetRecord

if TYPE_CHECKING:
    from collections.abc import Iterable

    from schemas.chat_broadcast_records import TargetSeed

_ROSTER = _chat_broadcast_accounts
_TARGETS = _chat_broadcast_targets


def _list_roster(campaign_id: str) -> list[RosterAccount]:
    statement = (
        select(_ROSTER).where(_ROSTER.c.campaign_id == campaign_id).order_by(_ROSTER.c.position)
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [RosterAccount.model_validate(dict(row)) for row in rows]


async def list_roster(campaign_id: str) -> list[RosterAccount]:
    return await asyncio.to_thread(_list_roster, campaign_id)


def _update_roster(campaign_id: str, account_id: str, fields: dict[str, object]) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_ROSTER)
            .where(_ROSTER.c.campaign_id == campaign_id, _ROSTER.c.account_id == account_id)
            .values(**fields),
        )


async def halt_account(campaign_id: str, account_id: str, reason: str) -> None:
    """Take an account out of this campaign for good (spam block, errors in a row)."""
    await asyncio.to_thread(
        _update_roster, campaign_id, account_id, {"state": "halted", "halted_reason": reason}
    )


async def set_consecutive_errors(campaign_id: str, account_id: str, count: int) -> None:
    await asyncio.to_thread(_update_roster, campaign_id, account_id, {"consecutive_errors": count})


def _list_targets(campaign_id: str) -> list[TargetRecord]:
    statement = (
        select(_TARGETS).where(_TARGETS.c.campaign_id == campaign_id).order_by(_TARGETS.c.position)
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [TargetRecord.model_validate(dict(row)) for row in rows]


async def list_targets(campaign_id: str) -> list[TargetRecord]:
    return await asyncio.to_thread(_list_targets, campaign_id)


def _fetch_target(campaign_id: str, chat_key: str) -> TargetRecord | None:
    statement = select(_TARGETS).where(
        _TARGETS.c.campaign_id == campaign_id, _TARGETS.c.chat_key == chat_key
    )
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    return None if row is None else TargetRecord.model_validate(dict(row))


async def fetch_target(campaign_id: str, chat_key: str) -> TargetRecord | None:
    return await asyncio.to_thread(_fetch_target, campaign_id, chat_key)


def _replace_targets(campaign_id: str, seeds: list[TargetSeed]) -> None:
    now = int(time.time())
    with _get_engine().begin() as connection:
        connection.execute(delete(_TARGETS).where(_TARGETS.c.campaign_id == campaign_id))
        for position, seed in enumerate(seeds):
            connection.execute(
                insert(_TARGETS).values(
                    campaign_id=campaign_id,
                    position=position,
                    updated_unix=now,
                    **seed.model_dump(),
                ),
            )


async def replace_targets(campaign_id: str, seeds: list[TargetSeed]) -> None:
    """A fresh run's chats, in order; whatever the previous run left is dropped."""
    await asyncio.to_thread(_replace_targets, campaign_id, seeds)


def _merge_targets(campaign_id: str, seeds: list[TargetSeed]) -> None:
    now = int(time.time())
    keys = {seed.chat_key for seed in seeds}
    with _get_engine().begin() as connection:
        existing = {
            str(key): int(position)
            for key, position in connection.execute(
                select(_TARGETS.c.chat_key, _TARGETS.c.position).where(
                    _TARGETS.c.campaign_id == campaign_id
                )
            ).all()
        }
        connection.execute(
            update(_TARGETS)
            .where(
                _TARGETS.c.campaign_id == campaign_id,
                _TARGETS.c.chat_key.not_in(keys),
                _TARGETS.c.state.not_in(("done", "skipped")),
            )
            .values(state="skipped", skip_reason="removed", updated_unix=now),
        )
        position = max(existing.values(), default=-1)
        for seed in seeds:
            if seed.chat_key in existing:
                continue
            position += 1
            connection.execute(
                insert(_TARGETS).values(
                    campaign_id=campaign_id,
                    position=position,
                    updated_unix=now,
                    **seed.model_dump(),
                ),
            )


async def merge_targets(campaign_id: str, seeds: list[TargetSeed]) -> None:
    """A resumed run's chats: new ones appended, removed ones skipped, the rest kept."""
    await asyncio.to_thread(_merge_targets, campaign_id, seeds)


def _update_target(
    campaign_id: str,
    chat_key: str,
    expect_states: Iterable[str] | None,
    fields: dict[str, object],
) -> bool:
    statement = update(_TARGETS).where(
        _TARGETS.c.campaign_id == campaign_id, _TARGETS.c.chat_key == chat_key
    )
    if expect_states is not None:
        statement = statement.where(_TARGETS.c.state.in_(tuple(expect_states)))
    with _get_engine().begin() as connection:
        result = connection.execute(statement.values(updated_unix=int(time.time()), **fields))
    return result.rowcount > 0


async def update_target(
    campaign_id: str,
    chat_key: str,
    *,
    expect_states: Iterable[str] | None = None,
    **fields: object,
) -> bool:
    """Write one chat's fields; with ``expect_states``, only while it is in one of them."""
    states = None if expect_states is None else tuple(expect_states)
    return await asyncio.to_thread(_update_target, campaign_id, chat_key, states, fields)


def _start_round(campaign_id: str, round_number: int) -> int:
    """Re-queue every chat that finished the round (and every chat an error skipped)."""
    with _get_engine().begin() as connection:
        finished = connection.execute(
            update(_TARGETS)
            .where(
                _TARGETS.c.campaign_id == campaign_id,
                (_TARGETS.c.state == "round_done")
                | ((_TARGETS.c.state == "skipped") & (_TARGETS.c.skip_reason == "error")),
            )
            .values(
                state="queued",
                skip_reason=None,
                round=round_number,
                step_index=0,
                next_action_unix=None,
                updated_unix=int(time.time()),
            ),
        )
    return int(finished.rowcount)


async def start_round(campaign_id: str, round_number: int) -> int:
    return await asyncio.to_thread(_start_round, campaign_id, round_number)


def _finish_rounds(campaign_id: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_TARGETS)
            .where(_TARGETS.c.campaign_id == campaign_id, _TARGETS.c.state == "round_done")
            .values(state="done", updated_unix=int(time.time())),
        )


async def finish_rounds(campaign_id: str) -> None:
    """The last round is over: every chat that completed it is done."""
    await asyncio.to_thread(_finish_rounds, campaign_id)


def _settle_interrupted(campaign_id: str) -> None:
    """After a crash, a chat caught mid-join or mid-captcha starts that step again."""
    now = int(time.time())
    with _get_engine().begin() as connection:
        connection.execute(
            update(_TARGETS)
            .where(_TARGETS.c.campaign_id == campaign_id, _TARGETS.c.state == "joining")
            .values(state="queued", updated_unix=now),
        )
        connection.execute(
            update(_TARGETS)
            .where(
                _TARGETS.c.campaign_id == campaign_id,
                _TARGETS.c.state.in_(("captcha", "reconnecting")),
            )
            .values(state="waiting", next_action_unix=now, updated_unix=now),
        )


async def settle_interrupted(campaign_id: str) -> None:
    await asyncio.to_thread(_settle_interrupted, campaign_id)
