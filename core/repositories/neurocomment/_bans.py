"""Auto-ban readiness write (#30): mark a (account, channel) pair banned.

Split from ``_comments.py`` for the file-size budget (mirrors ``_deletions.py``).
A ban is PERMANENT, by product decision — a channel that banned an account is closed
to it for good, and the operator's remedy is another account, not a way back. So there
is deliberately no un-ban here: ``upsert_readiness`` never touches ``banned`` (a
re-onboard cannot revive the pair), and only ``delete_readiness`` — which drops the row
entirely — clears it. The live can_send probe behind "Проверить каналы" used to lift a
ban; it was removed rather than left contradicting what the UI now tells the operator.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import update

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
