"""Periodic deletion sweep (#131) — split out of ``services.neurocomment._runtime``.

The sweep *work* (the periodic loop + per-sweep pass + per-channel check) lives
here to keep ``_runtime`` under the aislop file-size cap. The task handle and its
start/stop stay in ``_runtime`` (its lifecycle owns reconcile/shutdown); these
functions are re-exported there so ``_runtime._sweep_*`` still resolves for
callers and tests.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    fetch_active_campaigns_for_channels,
    list_active_watch_channels,
    list_campaign_accounts,
    list_channel_readiness,
    list_pending_join_readiness,
    list_posted_comments_since,
    mark_comments_deleted,
    purge_neurocomment_history_older_than,
)
from core.logging import log_event
from core.telegram_client import TelegramReadError
from schemas.telegram_actions import CheckMessagesAlive, CheckMessagesAliveResult
from services.neurocomment import _rejoin, _seams, _state
from services.neurocomment._pins import serving_accounts

if TYPE_CHECKING:
    from schemas.neurocomment import CommentRecord, NeurocommentReadiness

# Stdlib sink for full third-party text — see ``core.proxy_check._failed_result``.
logger = logging.getLogger(__name__)

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

    The lone non-event loop in the runtime, and it must never die (mirrors the
    listener-safe on-post pipeline). The retention prune, the join-request review and the
    access-loss review piggyback on the same tick, and every pass is awaited behind the
    ONE guard below rather than behind the ones inside it: each of those covers only its
    own first bulk read, so everything after it — a locked SQLite, a malformed timestamp,
    the live Telegram RPC ``deactivate_channel`` reaches through the listener reconcile —
    unwound into this loop body and ended the task for the rest of the process lifetime.
    Silently, too: the handle in ``_runtime`` carries no done-callback, so all four
    lifecycle rules simply stopped until an operator hit Start again. Guarding here is
    what makes the promise true — no pass can abort the loop or its siblings, and every
    fault leaves a ``neurocomment_sweep_failed`` line naming the pass that raised.
    """
    interval = settings.neurocomment.deletion_sweep_interval_seconds
    while True:
        await asyncio.sleep(interval)
        # One clock for the whole tick: these passes age the same deadlines, and four
        # separate reads of ``now`` only let them disagree about where one falls.
        now = datetime.now(UTC)
        # Callables, not coroutine objects: shutdown cancels this task mid-tick, and four
        # coroutines built up front leave the un-awaited ones to warn on collection.
        for name, run_pass in (
            ("deletion", _sweep_once),
            ("retention", partial(_prune_history_if_due, now)),
            ("join_requests", partial(_review_join_requests, now)),
            ("rejoin", partial(_rejoin.review_access_lost, now)),
        ):
            try:
                await run_pass()
            except Exception as exc:  # a pass fault must never kill the loop.
                logger.exception("neurocomment sweep pass %s failed", name)
                await log_event(
                    "WARNING",
                    "neurocomment_sweep_failed",
                    extra={"pass": name, "error_type": type(exc).__name__},
                )


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
    # Floored at one day because the join log is not pure ballast inside 24h: it backs the
    # rolling-24h per-account join count that ``_at_join_cap`` reads for the #270 anti-freeze
    # cap. ``retention_days`` is a float (0.5 is valid config), and a sub-day cutoff deletes
    # joins the count still needs — it then under-counts, the cap lets the account keep
    # joining, and Telegram freezes it. Flooring the *shared* cutoff rather than computing a
    # second one only ever lengthens comment/challenge retention, which is the safe direction.
    cutoff = (now - timedelta(days=max(nc.retention_days, 1.0))).isoformat()
    try:
        removed = await purge_neurocomment_history_older_than(cutoff)
    except Exception as exc:  # retention must never abort the deletion sweep.
        logger.exception("neurocomment retention purge failed")
        await log_event(
            "WARNING",
            "neurocomment_retention_purge_failed",
            extra={
                "event": "neurocomment_retention_purged",
                "error_type": type(exc).__name__,
            },
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


async def _review_join_requests(now: datetime) -> None:
    """Age the outstanding admin-approval join requests: retry the due ones, drop the dead.

    Rides this loop because onboarding has NO timer of its own — it runs on operator
    Start, on boot, and on campaign reconciles only — so a 24h/48h rule placed in the
    onboarding pass would simply never fire. Never raises: a failure here must not
    abort the deletion sweep that owns this tick.
    """
    nc = settings.neurocomment
    retry_after = timedelta(hours=nc.join_request_retry_hours)
    # The whole patience budget: every attempt gets its own retry window before we
    # accept that nobody is going to press Approve. It runs from the FIRST request and
    # never restarts — the operator's rule is "48 часов, если заявка не принимается,
    # канал удаляем; за эти 48 часов 2 заявки", i.e. two requests paced *inside* one 48h
    # wall clock. ``join_requested_at`` holds that first stamp (the repository coalesces
    # it), so re-sending can no longer push the deadline out a window at a time.
    give_up_after = retry_after * nc.join_request_max_attempts
    try:
        rows = (await list_pending_join_readiness()).readiness
    except Exception as exc:  # noqa: BLE001 - the review must never abort the sweep loop.
        await log_event(
            "WARNING",
            "neurocomment_join_request_review_failed",
            extra={"error_type": type(exc).__name__},
        )
        return
    by_channel: dict[str, list[NeurocommentReadiness]] = defaultdict(list)
    for row in rows:
        by_channel[row.channel].append(row)
    # Only channels still linked to an ACTIVE campaign can be dropped; the bulk read
    # also hands us the campaign id ``deactivate_channel`` needs.
    campaigns = await fetch_active_campaigns_for_channels(list(by_channel))
    retry_due = False
    for channel, channel_rows in by_channel.items():
        campaign = campaigns.get(channel)
        if campaign is None:
            continue
        # Resolved once and used by both halves of the decision below: the keep-check
        # read EVERY readiness row on the channel while the drop it guards resolved
        # serving accounts pin-aware, so a ready row belonging to an account since
        # removed from the campaign — or pinned to another channel entirely — kept a
        # channel alive that nobody serving it could reach.
        links = (await list_campaign_accounts(campaign.campaign_id)).links
        serving = serving_accounts(links, channel)
        if any(row.ready for row in channel_rows if row.account_id in serving):
            # One stubborn account must never kill a channel the other accounts comment
            # in fine — a ready serving pair is proof the channel works.
            continue
        ages = [
            (row, now - datetime.fromisoformat(row.join_requested_at))
            for row in channel_rows
            if row.join_requested_at is not None
        ]
        if all(age >= give_up_after for _row, age in ages):
            await _drop_unapproved_channel(campaign.campaign_id, channel, serving, len(ages))
            continue
        retry_due = retry_due or any(
            # One window per attempt already spent, all measured from the first request:
            # attempt 1 at t=0, attempt 2 at t=+24h. Comparing the age against a bare
            # ``retry_after`` was only ever right because the stamp used to move with
            # each attempt — anchored, it would authorize the next request the instant
            # the FIRST window lapsed, however many had gone out since.
            age >= retry_after * row.join_request_attempts
            and row.join_request_attempts < nc.join_request_max_attempts
            for row, age in ages
        )
    if retry_due:
        # Late import: ``_runtime`` imports this module, so a top-level import cycles.
        from services.neurocomment import _runtime, _signals  # noqa: PLC0415

        _runtime._ensure_onboarding_running(_signals.signal_onboarding_progress)  # noqa: SLF001


async def _drop_unapproved_channel(
    campaign_id: str,
    channel: str,
    serving: list[str],
    pending: int,
) -> None:
    """Unlink a channel nobody approved us into, via the service (so the listener reconciles).

    Gated on the same coverage rule as ``bans._unlink_channel_if_no_account_left`` and
    ``_rejoin._drop_channel_if_nothing_works``, because it had the defect both of those
    fixed: the rows above are every readiness row on the channel, whoever they belong to,
    so one expired request could drop a channel the campaign's other accounts had simply
    never been onboarded to. Every ``serving`` account must have a row before the channel
    can go (a missing row means "never tried here", not "failed here"), and any ready one
    keeps it. The caller resolves the pins, so its keep-check and this drop can no longer
    disagree about who serves the channel. One extra read per candidate channel, once per
    channel — this loop ticks every five minutes and is no hot path.
    """
    rows = (await list_channel_readiness(campaign_id, channel, serving)).readiness
    if len(rows) != len(serving) or any(row.ready for row in rows):
        return
    # Late import for the same cycle as above (campaigns -> _runtime -> _sweep).
    from services.neurocomment import campaigns as campaigns_service  # noqa: PLC0415

    await campaigns_service.deactivate_channel(campaign_id, channel)
    await log_event(
        "WARNING",
        "neurocomment_join_request_expired",
        extra={
            "channel": channel,
            "campaign_id": campaign_id,
            "pending_accounts": pending,
            "reason": "no_admin_approval",
        },
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
        # The gateway wraps every Telethon failure as TelegramReadError, so the class
        # name alone said nothing: 544 identical lines in three days and no way to tell
        # a flood-wait from a lost peer. ``reason`` carries the wrapped cause.
        extra: dict[str, object] = {"channel": channel, "error_type": type(exc).__name__}
        if isinstance(exc, TelegramReadError):
            extra |= {"reason": exc.reason, "kind": exc.kind}
        await log_event("WARNING", "neurocomment_sweep_read_failed", account_id=reader, extra=extra)
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
