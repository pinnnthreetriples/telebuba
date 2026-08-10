"""What happens to a pair that has run out of re-joins: it is marked, and it says so.

The channel drop in ``_rejoin`` reports the CHANNEL's fate and needs every serving
account to be finished with it, so an account dropping out of a chat the others comment
in fine was invisible — no log line, and the board reads that account's badge off the
channel's aggregate, which is still green «Готов».

It reports, and that is all. This path also used to leave the discussion group, and every
production row logged ``leave: "failed"`` — by construction: the state that brings a pair
here IS ``access_lost``, i.e. Telegram has already said the account is not in that chat.
The attempt is not free either, it is two RPCs (``_groups`` resolves the group, then sends
``LeaveChannelRequest``), and the resolve is the half that succeeds — so the second one is
a real knock on a group that ejected the account. The rare case where it WOULD succeed is
the one where it is actively wrong: a stale cached entity means the pair was never kicked,
and leaving walks a healthy account out of a live chat. ``_captcha_retry._give_up_and_
leave`` is the other side of that line and still leaves — its row is ``joined=1``, the
account really is in the group.

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

if TYPE_CHECKING:
    from schemas.neurocomment import NeurocommentReadiness


async def report(channel: str, rows: list[NeurocommentReadiness]) -> None:
    """Mark each pair finished with ``channel``, once, and log the verdict."""
    budget = settings.neurocomment.channel_max_rounds
    for row in rows:
        # Marked first, and the mark is what authorizes the line: the review runs every five
        # minutes, so without it the same pair would be logged again on every tick. The write
        # is conditional, so a pair the poked onboarding pass re-joined while this loop was
        # reporting OTHERS is left alone rather than badged for a chat it is back in — which
        # matters more since the mark became the verdict ``_rejoin.exhausted`` reads.
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
            },
        )
