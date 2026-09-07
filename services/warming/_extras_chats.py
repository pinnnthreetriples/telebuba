"""Chat extras runners — the channel-scoped writes (leave, archive, mute) and the poll vote.

Unlike :mod:`services.warming._extras_writes` these aim at a channel, so every pick
is confined to what the account already did on its own: a poll among the posts its
read just fetched, a leave / archive / mute among the channels warming itself joined
(``warming_joined_channels``). All four dispatch through ``_write`` so the daily
budget is booked before the RPC leaves the process.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import record_channel_left
from schemas.telegram_actions import LeaveChannel
from schemas.telegram_actions_warming import WarmMutePeer, WarmToggleArchive, WarmVoteInPoll
from services.warming import _seams
from services.warming._extras_ctx import _recent_posts, _write

if TYPE_CHECKING:
    from schemas._warming_extras import JoinedChannel
    from schemas.telegram_actions import ActionResult
    from services.warming._extras_ctx import _ExtraContext

# Telegram's answer cap; core wraps the draw modulo the poll's real answer count.
_POLL_OPTIONS_MAX = 10


def joined_now(ctx: _ExtraContext) -> list[JoinedChannel]:
    """Still-joined rows the channel writes may aim at — private invites excluded.

    A private invite (stored as ``+HASH``) is no peer ``get_input_entity`` can resolve,
    and leaving one is a one-way door: the same hash may not re-admit the account
    after the cooldown, so the channel would be lost for good.
    """
    return [j for j in ctx.joined if j.left_at is None and not j.channel.startswith("+")]


def _leave_candidates(ctx: _ExtraContext, now: datetime) -> list[str]:
    """Joined long enough and not read this very cycle; none while a leave is recent.

    The rate cap needs no state of its own: the newest ``left_at`` among the rows
    already says when the account last left something.
    """
    rest = now - timedelta(days=settings.warming.extras_leave_min_interval_days)
    if any(j.left_at is not None and datetime.fromisoformat(j.left_at) > rest for j in ctx.joined):
        return []
    cutoff = now - timedelta(days=settings.warming.extras_leave_min_age_days)
    reading = {c.channel for c in ctx.chosen}
    return [
        j.channel
        for j in joined_now(ctx)
        if datetime.fromisoformat(j.created_at) <= cutoff and j.channel not in reading
    ]


async def leave(ctx: _ExtraContext) -> ActionResult | None:
    candidates = _leave_candidates(ctx, datetime.now(UTC))
    if not candidates:
        return None
    channel = _seams.rng.choice(candidates)
    result = await _write(ctx, LeaveChannel(channel=channel))
    if result.status == "ok":
        await record_channel_left(ctx.account_id, channel)
    return result


async def archive(ctx: _ExtraContext) -> ActionResult:
    channel = _seams.rng.choice(joined_now(ctx)).channel
    archived = _seams.rng.random() < settings.warming.extras_archive_probability
    return await _write(ctx, WarmToggleArchive(channel=channel, archived=archived))


async def mute(ctx: _ExtraContext) -> ActionResult:
    channel = _seams.rng.choice(joined_now(ctx)).channel
    hours = _seams.rng.choice(settings.warming.extras_mute_hours)
    return await _write(ctx, WarmMutePeer(channel=channel, mute_hours=hours))


async def polls(ctx: _ExtraContext) -> ActionResult:
    channel, ids = _recent_posts(ctx)
    option = _seams.rng.randrange(_POLL_OPTIONS_MAX)
    return await _write(ctx, WarmVoteInPoll(channel=channel, message_ids=ids, option_index=option))
