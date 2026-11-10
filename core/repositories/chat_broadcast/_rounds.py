"""Chats keep their own rounds: promote, rest, move a rest, adopt a barrier, keep.

Every write is a compare-and-set on the round (and state) the chat is leaving, so two
callers — two workers, the redraw of a rest and the chat's own promotion — cannot put a
chat two rounds on or a rest into the middle of a round.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import select, update

from core.db import _get_engine
from core.repositories.chat_broadcast._tables import (
    _chat_broadcast_campaigns,
    _chat_broadcast_targets,
)

if TYPE_CHECKING:
    from sqlalchemy import ColumnElement

    from schemas.chat_broadcast_records import CampaignRecord

_TARGETS = _chat_broadcast_targets
_CAMPAIGNS = _chat_broadcast_campaigns


class _Promotion(NamedTuple):
    now_unix: int
    max_round: int | None


def _resting_chat() -> ColumnElement[bool]:
    """A chat out of the game for now that comes back by itself when its time is up."""
    skipped = _TARGETS.c.state == "skipped"
    return (
        (_TARGETS.c.state == "round_done")
        | (skipped & (_TARGETS.c.skip_reason == "error"))
        | (skipped & (_TARGETS.c.skip_reason == "deleted") & (_TARGETS.c.ignore_deleted == 1))
    )


def _promote_target(campaign_id: str, chat_key: str, round_number: int, due: _Promotion) -> bool:
    where = [
        _TARGETS.c.campaign_id == campaign_id,
        _TARGETS.c.chat_key == chat_key,
        _TARGETS.c.round == round_number,
        _TARGETS.c.next_action_unix.is_not(None),
        _TARGETS.c.next_action_unix <= due.now_unix,
        _resting_chat(),
    ]
    if due.max_round is not None:
        where.append(_TARGETS.c.round < due.max_round)
    with _get_engine().begin() as connection:
        result = connection.execute(
            update(_TARGETS)
            .where(*where)
            .values(
                state="queued",
                skip_reason=None,
                round=round_number + 1,
                step_index=0,
                next_action_unix=None,
                pinned=0,
                updated_unix=int(time.time()),
            ),
        )
    return result.rowcount > 0


async def promote_target(
    campaign_id: str,
    chat_key: str,
    *,
    round_number: int,
    now_unix: int,
    max_round: int | None = None,
) -> bool:
    """Open the next round of ONE chat whose rest is over; ``False`` when it was not due.

    Compare-and-set on the round the chat is leaving, so two workers promoting the same
    chat advance it by one round, not two. Re-queued: a chat that finished its chain, one
    an error skipped, and one skipped for deletions that the operator kept since; a new
    round also ends a pin. With ``max_round`` the last round is never left.
    """
    return await asyncio.to_thread(
        _promote_target, campaign_id, chat_key, round_number, _Promotion(now_unix, max_round)
    )


def _rest_target(campaign_id: str, chat_key: str, round_number: int, until_unix: int) -> bool:
    with _get_engine().begin() as connection:
        result = connection.execute(
            update(_TARGETS)
            .where(
                _TARGETS.c.campaign_id == campaign_id,
                _TARGETS.c.chat_key == chat_key,
                _TARGETS.c.round == round_number,
                _TARGETS.c.state == "writing",
            )
            .values(
                state="round_done",
                next_action_unix=until_unix,
                pinned=0,
                updated_unix=int(time.time()),
            ),
        )
    return result.rowcount > 0


async def rest_target(
    campaign_id: str, chat_key: str, *, round_number: int, until_unix: int
) -> bool:
    """A chat has sent its whole chain: it rests until ``until_unix``, then opens its next round.

    Only from ``writing`` and only in ``round_number``. A hand-over by the operator covered
    this round and is spent: the chat is no longer pinned to its account.
    """
    return await asyncio.to_thread(_rest_target, campaign_id, chat_key, round_number, until_unix)


def _move_target_rest(
    campaign_id: str, chat_key: str, round_number: int, until_unix: int, *, unscheduled: bool
) -> bool:
    where = [
        _TARGETS.c.campaign_id == campaign_id,
        _TARGETS.c.chat_key == chat_key,
        _TARGETS.c.round == round_number,
        _resting_chat(),
    ]
    if unscheduled:
        where.append(_TARGETS.c.next_action_unix.is_(None))
    with _get_engine().begin() as connection:
        result = connection.execute(
            update(_TARGETS)
            .where(*where)
            .values(next_action_unix=until_unix, updated_unix=int(time.time())),
        )
    return result.rowcount > 0


async def move_target_rest(
    campaign_id: str,
    chat_key: str,
    *,
    round_number: int,
    until_unix: int,
    unscheduled: bool = False,
) -> bool:
    """Move the end of one chat's rest; only while it still rests in ``round_number``.

    A chat that left its rest was re-queued in the next round: a move that lands after
    that must not put a rest into the middle of it. ``unscheduled`` writes only a rest
    with no end yet (a chat a barrier-round run left waiting).
    """
    return await asyncio.to_thread(
        lambda: _move_target_rest(
            campaign_id, chat_key, round_number, until_unix, unscheduled=unscheduled
        )
    )


def _adopt_barrier_rest(campaign_id: str) -> int:
    with _get_engine().begin() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        until = connection.execute(
            select(_CAMPAIGNS.c.rest_until_unix).where(_CAMPAIGNS.c.campaign_id == campaign_id)
        ).scalar_one_or_none()
        if until is None:
            return 0
        moved = connection.execute(
            update(_TARGETS)
            .where(
                _TARGETS.c.campaign_id == campaign_id,
                _TARGETS.c.next_action_unix.is_(None),
                _resting_chat(),
            )
            .values(next_action_unix=int(until), updated_unix=int(time.time())),
        )
        connection.execute(
            update(_CAMPAIGNS)
            .where(_CAMPAIGNS.c.campaign_id == campaign_id)
            .values(rest_until_unix=None),
        )
    return int(moved.rowcount)


async def adopt_barrier_rest(campaign_id: str) -> int:
    """Hand a campaign's one shared rest over to its chats; returns how many took it.

    A run that began before chats kept their own rounds left the end of its rest on the
    campaign. Every chat waiting for it now carries that end itself and the campaign
    carries none — one transaction, so a restart in between can neither lose the rest
    nor stretch it.
    """
    return await asyncio.to_thread(_adopt_barrier_rest, campaign_id)


def _keep_target(campaign_id: str, chat_key: str, status: str, fields: dict[str, object]) -> bool:
    with _get_engine().begin() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        current = connection.execute(
            select(_CAMPAIGNS.c.status).where(_CAMPAIGNS.c.campaign_id == campaign_id)
        ).scalar_one_or_none()
        if current is None or str(current) != status:
            return False
        result = connection.execute(
            update(_TARGETS)
            .where(
                _TARGETS.c.campaign_id == campaign_id,
                _TARGETS.c.chat_key == chat_key,
                _TARGETS.c.state == "skipped",
                _TARGETS.c.skip_reason == "deleted",
            )
            .values(ignore_deleted=1, updated_unix=int(time.time()), **fields),
        )
    return result.rowcount > 0


async def keep_target(seen: CampaignRecord, chat_key: str, **fields: object) -> bool:
    """Keep a chat skipped for deletions: set the flag and ``fields`` in one transaction.

    Only while the campaign still has the status ``seen`` caught it in. The write and the
    run's own finish (``try_finish``) both take the database's write lock, so whichever
    comes second sees the other: a finish that came first leaves the chat flagged only, a
    keep that came first leaves an open chat for the finish to find.
    """
    return await asyncio.to_thread(_keep_target, seen.campaign_id, chat_key, seen.status, fields)
