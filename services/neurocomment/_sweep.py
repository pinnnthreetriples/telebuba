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
    fetch_active_campaigns_for_channels,
    list_active_watch_channels,
    list_posted_comments_since,
    mark_comments_deleted,
    purge_neurocomment_history_older_than,
)
from core.logging import log_event
from schemas.telegram_actions import CheckMessagesAlive, CheckMessagesAliveResult
from services.neurocomment import _seams, _state

if TYPE_CHECKING:
    from schemas.neurocomment import CommentRecord

# When the retention prune last ran. The sweep ticks every ~5 minutes, but a delete
# scan over the append-only tables is far too expensive at that cadence, so the prune
# rides this loop and self-gates on ``retention_prune_interval_hours``. ``None`` = never
# ran in this process, so the first tick after start prunes.
_LAST_PRUNE_AT: datetime | None = None


def reset_prune_clock() -> None:
    """Forget when the prune last ran (test seam — the next pass becomes due)."""
    global _LAST_PRUNE_AT  # noqa: PLW0603 - module-level clock, same shape as the task handles.
    _LAST_PRUNE_AT = None


async def _sweep_loop() -> None:
    """Re-read recent comments on an interval; back off channels with mass deletions.

    The lone non-event loop in the runtime. A sweep fault is logged and the loop
    keeps going — it must never die (mirrors the listener-safe on-post pipeline).
    The retention prune piggybacks on the same tick behind its own guard, so neither
    half of the pass can abort the other.
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
        await _prune_history_if_due(datetime.now(UTC))


async def _prune_history_if_due(now: datetime) -> None:
    """Retention pass over the append-only neurocomment tables, at most once per interval.

    Skipped entirely when ``retention_days`` is 0 (keep forever). Never raises: retention
    is nice-to-have bookkeeping, so a failure is logged and swallowed rather than allowed
    to kill the sweep loop that owns deletion detection. The clock is stamped *before* the
    purge so a persistently failing prune retries on the next interval instead of
    re-scanning (and re-logging) every five minutes.
    """
    global _LAST_PRUNE_AT  # noqa: PLW0603 - module-level clock, same shape as the task handles.
    nc = settings.neurocomment
    if nc.retention_days <= 0:
        return
    interval_seconds = nc.retention_prune_interval_hours * 3600
    if _LAST_PRUNE_AT is not None and (now - _LAST_PRUNE_AT).total_seconds() < interval_seconds:
        return
    _LAST_PRUNE_AT = now
    cutoff = (now - timedelta(days=nc.retention_days)).isoformat()
    try:
        removed = await purge_neurocomment_history_older_than(cutoff)
    except Exception as exc:  # noqa: BLE001 - retention must never abort the deletion sweep.
        await log_event(
            "WARNING",
            "retention_purge_failed",
            extra={"event": "neurocomment_retention_purged", "error": str(exc)},
        )
        return
    if removed:
        # Only when rows actually went, mirroring ``neurocomment_comment_deleted``: an
        # idle deployment would otherwise log a no-op purge every day forever.
        await log_event(
            "INFO",
            "neurocomment_retention_purged",
            extra={"removed": removed, "cutoff": cutoff},
        )


async def _sweep_once() -> None:
    """One deletion pass: per active channel, count vanished comments → back off."""
    now = datetime.now(UTC)
    since_iso = (
        now - timedelta(hours=settings.neurocomment.deletion_sweep_lookback_hours)
    ).isoformat()
    # Group watched channels by active campaign so each campaign's recent comments
    # are read once, then bucketed back per channel for the deletion check. One bulk
    # query maps the whole watch set to its active campaigns (no per-channel fan-out);
    # a channel with no active campaign is absent from the map and dropped, as before.
    watch_channels = (await list_active_watch_channels()).channels
    campaigns = await fetch_active_campaigns_for_channels(watch_channels)
    by_campaign: dict[str, list[str]] = defaultdict(list)
    for channel in watch_channels:
        campaign = campaigns.get(channel)
        if campaign is not None:
            by_campaign[campaign.campaign_id].append(channel)
    for campaign_id, channels in by_campaign.items():
        comments = (await list_posted_comments_since(campaign_id, since_iso)).comments
        buckets: dict[str, list[CommentRecord]] = defaultdict(list)
        for comment in comments:
            buckets[comment.channel].append(comment)
        for channel in channels:
            try:
                await _sweep_channel(channel, buckets.get(channel, []), now)
            except Exception as exc:  # noqa: BLE001 - one channel must not abort the pass.
                await log_event(
                    "WARNING",
                    "neurocomment_sweep_channel_failed",
                    extra={"channel": channel, "error_type": type(exc).__name__},
                )


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
    # Stamp the vanished comments so the feed/history can show which were removed;
    # log only the freshly-marked ones (idempotent across the overlapping window).
    newly_deleted = (await mark_comments_deleted(channel, list(result.missing_ids))).comments
    if newly_deleted:
        await log_event(
            "WARNING",
            "neurocomment_comment_deleted",
            extra={"channel": channel, "count": len(newly_deleted)},
        )
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
