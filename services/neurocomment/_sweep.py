"""Periodic deletion sweep (#131) — split out of ``services.neurocomment._runtime``.

The sweep *work* (the periodic loop + per-sweep pass + per-channel check) lives
here to keep ``_runtime`` under the aislop file-size cap. The task handle and its
start/stop stay in ``_runtime`` (its lifecycle owns reconcile/shutdown); these
functions are re-exported there so ``_runtime._sweep_*`` still resolves for
callers and tests.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    fetch_active_campaign_for_channel,
    list_active_watch_channels,
    list_posted_comments_since,
)
from core.logging import log_event
from schemas.telegram_actions import CheckMessagesAlive, CheckMessagesAliveResult
from services.neurocomment import _seams, _state

if TYPE_CHECKING:
    from schemas.neurocomment import CommentRecord


async def _sweep_loop() -> None:
    """Re-read recent comments on an interval; back off channels with mass deletions.

    The lone non-event loop in the runtime. A sweep fault is logged and the loop
    keeps going — it must never die (mirrors the listener-safe on-post pipeline).
    """
    interval = settings.neurocomment.deletion_sweep_interval_seconds
    while True:
        await asyncio.sleep(interval)
        try:
            await _sweep_once()
        except Exception as exc:  # noqa: BLE001 - a sweep fault must never kill the loop.
            await log_event(
                "WARNING",
                "neurocomment_sweep_failed",
                extra={"error_type": type(exc).__name__, "message": str(exc)},
            )


async def _sweep_once() -> None:
    """One deletion pass: per active channel, count vanished comments → back off."""
    now = datetime.now(UTC)
    since_iso = (
        now - timedelta(hours=settings.neurocomment.deletion_sweep_lookback_hours)
    ).isoformat()
    # Group watched channels by active campaign so each campaign's recent comments
    # are read once, then bucketed back per channel for the deletion check.
    by_campaign: dict[str, list[str]] = defaultdict(list)
    for channel in (await list_active_watch_channels()).channels:
        campaign = await fetch_active_campaign_for_channel(channel)
        if campaign is not None:
            by_campaign[campaign.campaign_id].append(channel)
    for campaign_id, channels in by_campaign.items():
        comments = (await list_posted_comments_since(campaign_id, since_iso)).comments
        buckets: dict[str, list[CommentRecord]] = defaultdict(list)
        for comment in comments:
            buckets[comment.channel].append(comment)
        for channel in channels:
            await _sweep_channel(channel, buckets.get(channel, []), now)


async def _sweep_channel(channel: str, comments: list[CommentRecord], now: datetime) -> None:
    """Re-read one channel's recent comments; trip its back-off if too many are gone."""
    if _state.channel_in_backoff(channel, now):
        # Already cooled — skip the read and don't re-escalate. The same vanished
        # comments stay in the lookback window for hours, so re-counting them every
        # sweep would walk the back-off to its cap from a single deletion episode;
        # escalation must advance only after a cooldown lapses and deletions persist.
        return
    msg_ids = [c.comment_msg_id for c in comments if c.comment_msg_id is not None]
    if not msg_ids:
        return
    nc = settings.neurocomment
    # ponytail: reads as one comment-author (a group member). If that account was
    # later kicked, get_messages may report all ids gone (false trip) or raise (handled
    # below); add a reader quorum / membership check only if the canary shows false trips.
    reader = comments[0].account_id
    try:
        result = await _seams.execute_read(
            reader,
            CheckMessagesAlive(channel=channel, message_ids=msg_ids),
        )
    except Exception as exc:  # noqa: BLE001 - one channel's read must not abort the sweep.
        await log_event(
            "WARNING",
            "neurocomment_sweep_read_failed",
            account_id=reader,
            extra={"channel": channel, "error_type": type(exc).__name__},
        )
        return
    if not isinstance(result, CheckMessagesAliveResult):  # pragma: no cover - typed gateway
        return
    seconds = _state.register_channel_deletions(
        channel,
        now,
        _state.ChannelDeletionScan(set(msg_ids), set(result.missing_ids)),
        min_deletions=nc.channel_backoff_min_deletions,
        base_seconds=nc.channel_backoff_base_seconds,
        max_seconds=nc.channel_backoff_max_seconds,
    )
    if seconds is not None:
        await log_event(
            "WARNING",
            "neurocomment_channel_backoff",
            extra={
                "channel": channel,
                "missing": len(result.missing_ids),
                "cooldown_seconds": seconds,
            },
        )
