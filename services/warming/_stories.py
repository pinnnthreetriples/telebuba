"""Story-view cycle step — watch a subscribed peer's stories once per session.

Split out of :mod:`services.warming._cycle` to keep that module under the
file-size budget. A low-risk, very human warming signal (every persona); a plain
failure (peer has no stories / restricted) is not a health signal, so only a
rate-limit halts the cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from schemas.telegram_actions import WatchPeerStories
from services.warming import _seams
from services.warming.pacing import _WAIT_STATUSES, _classify_flood

if TYPE_CHECKING:
    from schemas.warming import WarmingChannel
    from services.warming._cycle import _ChannelTally


async def maybe_watch_stories(
    account_id: str,
    chosen: list[WarmingChannel],
    tally: _ChannelTally,
    *,
    can_attempt: bool,
) -> None:
    """View one chosen peer's stories, folding any rate-limit into ``tally``.

    A no-op (leaving ``tally`` untouched) when story viewing is disabled, no
    channel was chosen, the daily budget is spent, or the cycle already hit a
    flood — so the caller need not re-check those before calling.
    """
    warm = settings.warming
    if not (
        warm.story_view_enabled
        and chosen
        and can_attempt
        and not tally.flooded
        and not tally.peer_flooded
    ):
        return
    result = await _seams.execute(
        account_id,
        WatchPeerStories(peer=_seams.rng.choice(chosen).channel),
    )
    tally.attempts += 1
    if result.status == "peer_flood":
        tally.peer_flooded = True
        tally.last_failed_action = "watch_peer_stories"
    elif result.status in _WAIT_STATUSES:
        tally.flooded, tally.flood_seconds, tally.flood_until = _classify_flood(result)
        tally.last_failed_action = "watch_peer_stories"
