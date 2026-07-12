"""Auto-ban readiness writes (#30): mark a (account, channel) pair banned / clear it.

Split from ``_comments.py`` for the file-size budget (mirrors ``_deletions.py``).
A ban is sticky — ``upsert_readiness`` never touches ``banned``, so a re-onboard
can't revive it; only ``clear_pair_banned`` (a live can_send probe) or
``delete_readiness`` (operator retry) removes it.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import case, update

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


def _clear_pair_banned(account_id: str, channel: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_neurocomment_readiness)
            .where(
                # Only un-ban an actually-banned row; a can_send probe on a normal pair
                # must not spuriously flip its readiness.
                (_neurocomment_readiness.c.account_id == account_id)
                & (_neurocomment_readiness.c.channel == channel)
                & (_neurocomment_readiness.c.banned == 1),
            )
            .values(
                banned=0,
                # can_send restores selectability — UNLESS the operator also skipped the
                # pair, in which case the skip outlives the un-ban (else the board would
                # read it "ready" while the engine still excludes it via human_skipped).
                ready=case((_neurocomment_readiness.c.human_skipped == 1, 0), else_=1),
                checked_at=_now_iso(),
            ),
        )


async def clear_pair_banned(account_id: str, channel: str) -> None:
    """Lift an auto-ban after a live probe confirms the account can send again.

    ``can_send`` is direct proof of comment-ability, so the pair is restored to
    selectable (banned=0, ready=1) immediately — no re-onboard. No-op if not banned.
    """
    await asyncio.to_thread(_clear_pair_banned, account_id, channel)
