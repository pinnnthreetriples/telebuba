"""Comment threads and public groups in a warming visit (Прогрев 2.0, item 4.6).

Sometimes, after reading a channel, the account opens the comments under one of the
posts it just saw: a ``WarmReadDiscussion`` that reads and marks the thread read
without joining the linked group. A binge does it far more often; a glance never.

Public groups of the account's language sit in the same pool (``kind="group"``). They
are only walked from ``group_min_phase`` on, reacted to ``group_reaction_factor`` as
often as channels, and never written in. Their joins ride the channel walk, so they
count against the same per-phase rolling-24h join cap. Pending pool rows (a search's
proposals awaiting the operator, 4.5) are never walked at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from schemas.telegram_actions_warming_social import WarmReadDiscussion
from services.warming import _seams
from services.warming._phases import _PHASE_ORDER
from services.warming.pacing import _HALT_STATUSES, _classify_flood

if TYPE_CHECKING:
    from schemas.warming import WarmingChannel, WarmingPhase
    from services.warming._steps import _ChannelTally


def walkable_channels(channels: list[WarmingChannel], phase: WarmingPhase) -> list[WarmingChannel]:
    """The pool a visit may draw from: approved rows, groups only from their phase on."""
    groups_ok = _PHASE_ORDER.index(phase) >= _PHASE_ORDER.index(settings.warming.group_min_phase)
    return [
        channel
        for channel in channels
        if channel.status == "approved" and (groups_ok or channel.kind != "group")
    ]


def reaction_chance(base: float, channel: WarmingChannel) -> float:
    """A group is reacted to less often than a channel."""
    return base * settings.warming.group_reaction_factor if channel.kind == "group" else base


def _discussion_chance(visit_kind: str) -> float:
    warm = settings.warming
    if visit_kind == "binge":
        return warm.discussion_binge_probability
    if visit_kind == "normal":
        return warm.discussion_probability
    return 0.0


async def maybe_read_discussion(
    account_id: str,
    channel: WarmingChannel,
    recent_ids: list[int],
    tally: _ChannelTally,
    visit_kind: str,
) -> bool:
    """Maybe open the comments under a post just read. True when the walk must stop.

    Booked like every other channel request, immediately before dispatch (#208). Only
    broadcast channels have comment threads to open; a group's messages are its
    "comments" already.
    """
    if channel.kind == "group" or not recent_ids:
        return False
    if _seams.rng.random() >= _discussion_chance(visit_kind):
        return False
    tally.attempts += 1
    result = await _seams.execute(
        account_id,
        WarmReadDiscussion(channel=channel.channel, message_id=_seams.rng.choice(recent_ids)),
    )
    if result.status not in _HALT_STATUSES:
        return False
    if result.status == "peer_flood":
        tally.peer_flooded = True
    else:
        tally.flooded, tally.flood_seconds, tally.flood_until = _classify_flood(result)
    tally.last_failed_action = result.action_type
    tally.last_failed_channel = channel.channel
    return True
