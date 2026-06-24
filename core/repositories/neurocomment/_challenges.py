"""Challenge audit-and-cache queries (Ф2 #120).

One table backs both the audit log and the global solved-decision cache. This
slice (#145) is detection-only: ``insert_challenge`` appends a row and
``list_failed_for_channel`` powers the operator drill-down. The cache-lookup +
outcome-resolution readers land with the solver slice.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from sqlalchemy import select

from core.db import _get_engine, _now_iso
from core.repositories.neurocomment._tables import _neurocomment_challenges
from schemas.challenge import (
    ChallengedChannels,
    ChallengeInsert,
    ChallengeRow,
    ChallengeRowList,
)

if TYPE_CHECKING:
    from sqlalchemy import RowMapping

# Non-solved outcomes the drill-down surfaces ("what broke the solver"); a
# resolved/pending row is not a failure to show.
_FAILED_OUTCOMES = ("give_up", "failed")


def _insert_challenge(row: ChallengeInsert) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            _neurocomment_challenges.insert().values(
                challenge_hash=row.challenge_hash,
                account_id=row.account_id,
                channel=row.channel,
                raw_text=row.raw_text,
                button_labels_json=json.dumps(row.button_labels, ensure_ascii=False),
                decision_json=row.decision_json,
                outcome=row.outcome,
                decided_at=_now_iso(),
                outcome_at=None,
            ),
        )


async def insert_challenge(row: ChallengeInsert) -> None:
    """Append one challenge audit row (audit + global cache share this table)."""
    await asyncio.to_thread(_insert_challenge, row)


def _row_to_challenge(row: RowMapping) -> ChallengeRow:
    return ChallengeRow(
        account_id=str(row["account_id"]),
        channel=str(row["channel"]),
        raw_text=str(row["raw_text"]),
        button_labels=list(json.loads(row["button_labels_json"])),
        outcome=str(row["outcome"]),
        decided_at=str(row["decided_at"]),
    )


def _list_failed_for_channel(channel: str, limit: int) -> ChallengeRowList:
    # Order by id as the tiebreaker: same-microsecond inserts still come back
    # newest-first deterministically.
    statement = (
        select(_neurocomment_challenges)
        .where(
            (_neurocomment_challenges.c.channel == channel)
            & _neurocomment_challenges.c.outcome.in_(_FAILED_OUTCOMES),
        )
        .order_by(
            _neurocomment_challenges.c.decided_at.desc(),
            _neurocomment_challenges.c.id.desc(),
        )
        .limit(limit)
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return ChallengeRowList(rows=[_row_to_challenge(row) for row in rows])


async def list_failed_for_channel(channel: str, limit: int) -> ChallengeRowList:
    """Most-recent non-solved challenges for a channel (operator drill-down)."""
    return await asyncio.to_thread(_list_failed_for_channel, channel, limit)


def _list_challenged_channels(channels: list[str]) -> ChallengedChannels:
    if not channels:
        return ChallengedChannels()
    statement = (
        select(_neurocomment_challenges.c.channel)
        .where(
            _neurocomment_challenges.c.channel.in_(channels)
            & _neurocomment_challenges.c.outcome.in_(_FAILED_OUTCOMES),
        )
        .distinct()
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).all()
    return ChallengedChannels(channels=[str(row[0]) for row in rows])


async def list_challenged_channels(channels: list[str]) -> ChallengedChannels:
    """Which of ``channels`` carry a non-solved challenge (bulk board signal)."""
    return await asyncio.to_thread(_list_challenged_channels, channels)
