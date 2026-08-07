"""What happens to a pair that has run out of re-joins: it leaves, and it says so.

The channel drop in ``_rejoin`` reports the CHANNEL's fate and needs every serving
account to be finished with it, so an account dropping out of a chat the others comment
in fine was invisible — no log line, and the board reads that account's badge off the
channel's aggregate, which is still green «Готов».

Its own module so ``_rejoin`` stays under the file-size cap, and it takes the rows to
report rather than the predicates that pick them: deciding WHICH pairs are finished is
the re-join rule's job (see ``_rejoin._review_channel``, which owns the two exemptions),
executing the consequence is this one's. That split is also what keeps the import one-way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from core.db import mark_rejoin_gave_up
from core.logging import log_event
from schemas.telegram_actions import LeaveDiscussionGroup
from services.neurocomment import _seams

if TYPE_CHECKING:
    from schemas.neurocomment import NeurocommentReadiness


async def report(channel: str, rows: list[NeurocommentReadiness]) -> None:
    """Leave ``channel``'s discussion group for each pair, once, and log the verdict."""
    budget = settings.neurocomment.channel_max_rounds
    for row in rows:
        # Marked first, and the mark is what authorizes the rest: a failure below must not
        # buy the pair a second line next tick, and the write is conditional, so a pair the
        # poked onboarding pass re-joined while this loop was leaving OTHER chats is left
        # alone instead of being walked back out of the one it just re-entered.
        if not await mark_rejoin_gave_up(row.account_id, channel):
            continue
        await log_event(
            "WARNING",
            "neurocomment_rejoin_gave_up",
            account_id=row.account_id,
            extra={
                "channel": channel,
                "attempts": row.rejoin_attempts,
                # Clamped like the attempt line's counter: a stale stamp buys an attempt
                # on the CURRENT budget, so the raw count can outrun it.
                "reason": f"{min(row.rejoin_attempts, budget)}/{budget}",
                "leave": await _leave_quietly(row.account_id, channel),
            },
        )


async def _leave_quietly(account_id: str, channel: str) -> str:
    """Leave the channel's discussion group; return the outcome, never raise.

    ``bans._mark_banned_and_leave``'s contract, for its reason: the pair is finished here
    either way, so a leave that fails costs a log field and nothing else — never a
    readiness write, never a cooldown. It usually DOES fail: the account is out of the
    chat already, which is what the whole re-join budget was spent proving.
    """
    try:
        result = await _seams.execute(account_id, LeaveDiscussionGroup(channel=channel))
    except Exception as exc:  # noqa: BLE001 - the mark stands; the leave is best-effort.
        return type(exc).__name__
    return result.status
