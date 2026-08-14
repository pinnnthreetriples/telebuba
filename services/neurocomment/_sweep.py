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
    list_delivered_comments_since,
    list_pending_join_readiness,
    mark_comments_deleted,
    purge_neurocomment_history_older_than,
    reclaim_stale_claims,
)
from core.logging import log_event
from services.neurocomment import (
    _captcha_retry,
    _channel_pause,
    _inactive,
    _rejoin,
    _reply_wait,
    _sweep_read,
)
from services.neurocomment._pins import serving_accounts
from services.neurocomment._seams import NeurocommentLeaseRevokedError

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
    """Re-read recent comments on an interval and record the ones that have vanished.

    The lone non-event loop in the runtime, and it must never die (mirrors the
    listener-safe on-post pipeline). The retention prune, the join-request review, the
    access-loss review, the captcha give-up review, the write-blocked-channel review,
    the silent-channel review and the stale-claim reclaim piggyback on the same tick,
    and every pass is awaited behind the ONE guard below rather
    than behind the ones inside it: each of those covers only its own first bulk read, so
    everything after it — a locked SQLite, a malformed timestamp, the live Telegram RPC
    ``deactivate_channel`` reaches through the listener reconcile — unwound into this loop
    body and ended the task for the rest of the process lifetime. Silently, too: the handle
    in ``_runtime`` carries no done-callback, so every lifecycle rule simply stopped until
    an operator hit Start again. Guarding here is what makes the promise true — no pass can
    abort the loop or its siblings, and every fault leaves a ``neurocomment_sweep_failed``
    line naming the pass that raised.

    The one way a pass ends this loop is by stopping it on purpose: a drop rule that unlinks
    the LAST watch channel reconciles the listener, which unsubscribes and stops the sweep.
    That stop cannot cancel us from inside us (see ``_runtime._cancel_bounded``), so it
    clears the handle instead and we retire below — after the tick drains, so the siblings
    of the pass that dropped the channel still run and the drop's own log line is written.
    """
    interval = settings.neurocomment.deletion_sweep_interval_seconds
    while True:
        await asyncio.sleep(interval)
        # One clock for the whole tick: these passes age the same deadlines, and separate
        # reads of ``now`` only let them disagree about where one falls.
        now = datetime.now(UTC)
        # Callables, not coroutine objects: shutdown cancels this task mid-tick, and
        # coroutines built up front leave the un-awaited ones to warn on collection.
        for name, run_pass in (
            ("deletion", _sweep_once),
            ("retention", partial(_prune_history_if_due, now)),
            ("join_requests", partial(_review_join_requests, now)),
            ("rejoin", partial(_rejoin.review_access_lost, now)),
            # Immediately after its sibling: the two pair-level recovery rules read the
            # same readiness table and both must run BEFORE ``channel_pause`` re-reads its
            # deadlines, or a pause released later in the same tick would leave them
            # judging a window that no longer stands.
            ("captcha_retry", partial(_captcha_retry.review_captcha_blocked, now)),
            ("channel_pause", partial(_channel_pause.review_expired_pauses, now)),
            # LAST of the drop rules on purpose: the ones above judge a channel that
            # refuses us, and a channel they unlink this tick must not also be probed for
            # posts it will never be asked for again.
            ("silent_channels", partial(_inactive.review_silent_channels, now)),
            ("stale_claims", partial(_reclaim_stale_claims, now)),
        ):
            try:
                await run_pass()
            except NeurocommentLeaseRevokedError:
                return
            except Exception as exc:  # a pass fault must never kill the loop.
                logger.exception("neurocomment sweep pass %s failed", name)
                await log_event(
                    "WARNING",
                    "neurocomment_sweep_failed",
                    extra={"pass": name, "error_type": type(exc).__name__},
                )
        # Late import: ``_runtime`` imports this module, so a top-level import cycles.
        from services.neurocomment import _runtime  # noqa: PLC0415

        # De-registered mid-tick = a pass stopped us (last channel dropped, or the listener
        # account turned out to be warming). Retire instead of sweeping on for a listener
        # that is already unsubscribed. Identity, not ``is None``: a later pass in the same
        # tick can re-link and start a FRESH task, and then this one must yield to it.
        current = asyncio.current_task()
        if (
            _runtime._SWEEP_TASK is not current  # noqa: SLF001 - peer lifecycle handle
            or _runtime._SWEEP_STOPPING_TASK is current  # noqa: SLF001 - peer stop marker
        ):
            return


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
            await _drop_unapproved_channel(campaign.campaign_id, channel, serving, len(ages), now)
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
    now: datetime,
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

    A pair still working its way back into the chat keeps it too — the mirror of the hold
    ``_rejoin``'s own drop now grants an outstanding approval request, and the third corner
    of the concession ``_channel_pause`` already made both ways. Such a pair is neither ready
    nor absent, so it passed the coverage count as tried and held nothing: an account kicked
    while a LATER one's request was still out (onboarding reaches a fleet slowly — the
    rolling join cap, the join jitter) had its whole re-join budget annulled here with an
    attempt in flight. ``_rejoin.still_retrying`` is that rule's own give-up test, so this
    hold ends exactly where its does — a pair out of re-joins holds nothing, and the channel
    goes on the same tick it always did.
    """
    rows = (await list_channel_readiness(campaign_id, channel, serving)).readiness
    if (
        len(rows) != len(serving)
        or any(row.ready for row in rows)
        or any(_rejoin.access_lost(row) and _rejoin.still_retrying(row, now) for row in rows)
    ):
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


async def _reclaim_stale_claims(now: datetime) -> None:
    """Fail the claims no live worker can still be holding, so their quota slots come back.

    Rides this loop because the startup hook used to be the ONLY trigger. A worker that
    dies between winning the claim and resolving it — a crash, a kill, a task cancelled
    mid-flight — leaves the row ``claimed``, and ``_quota`` counts ``claimed`` alongside
    ``posted``: the account went on paying a day-cap slot on that channel (a THIRD of its
    day at the shipped cap of 3) for a comment it never sent, until the app happened to
    restart. On this tick the slot comes back within one cutoff instead.

    ``failed``, not ``release_claim``'s DELETE, and deliberately: the row is also the
    idempotency gate ``claim_comment`` wins, and unlike at startup the listener is LIVE
    while this runs — dropping the row would re-open the double-comment window if the same
    post were delivered again, or if the cutoff ever misfired against a slow-but-alive
    worker. ``release_claim`` is for callers that KNOW nothing was generated or sent; age
    alone cannot know that. ``failed`` frees the slot regardless, since quota counts only
    ``claimed``/``posted``, and it is the honest record of an attempt that never delivered.

    The cutoff is the startup one (``stale_claim_reclaim_seconds``, 900s), unchanged and
    unshared with the tick interval on purpose: the longest legitimate in-flight stretch at
    shipped defaults is ~4.5 minutes — three generate rounds of two 30s Gemini attempts plus
    backoff (183s), the reply delay (<=10s), the vision download (<=30s, bounded inside
    ``download_post_image`` precisely so this sum stays finite — unbounded it ran to ~34
    minutes on a slow proxy and outlived the cutoff), then the send (pool + RPC, ~50s) — so
    15 minutes still clears it three times over.

    Those are the DEFAULTS, though, and the cutoff is not what makes this safe: operator
    values inside the allowed ranges (the shared Gemini throttle, the reply delay) push a
    perfectly live attempt past 15 minutes, and age alone cannot tell it from a dead one.
    So the worker beats (``touch_comment_claim``) and this ages ``updated_at`` — a claim
    nobody is holding still ages exactly as it did. The beats bracket every long stretch:
    one per generation round, one per 60-second slice of the reply delay, and one last one
    gating the send, so the widest gap between two of them is a single ``generate_text``
    (~245s at the operator-settable ``le`` bounds) rather than the whole pipeline. What the
    beat cannot cover is a flood-wait Telethon sleeps off INSIDE the send RPC
    (``TELEGRAM__FLOOD_SLEEP_THRESHOLD``); a threshold above this cutoff still ends with a
    live send under a reclaimed row, which is why the send asks the beat first and abandons.
    """
    cutoff = (
        now - timedelta(seconds=settings.neurocomment.stale_claim_reclaim_seconds)
    ).isoformat()
    reclaimed = await reclaim_stale_claims(cutoff)
    if reclaimed:
        # Only when rows actually went (same rule as the prune above): a healthy
        # deployment must not log a no-op reclaim every five minutes forever.
        await log_event("INFO", "neurocomment_stale_claims_reclaimed", extra={"count": reclaimed})


async def _sweep_once() -> None:
    """One deletion pass: per active channel, stamp the comments that have vanished.

    Then the ``reply``-mode wait, which rides this pass rather than the loop's own list
    because it is the same shape of work — revisit rows nobody else will come back to. There
    is no pass at startup (only the stale-claim reclaim runs there, and this loop sleeps a
    full interval before its first tick), so a restart mid-wait is survived by the queue
    being the ``waiting`` rows themselves: the first tick finds every parked post, deadline
    already gone by or not.
    """
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
        # Every comment that reached Telegram, not only the ones recorded as ``posted``:
        # a row mis-classified ``failed`` (its claim reclaimed mid-send, or a crash between
        # the send and the commit) is still a live comment under a post, and this scan is
        # the only thing that can notice it being deleted.
        comments = (await list_delivered_comments_since(campaign_id, since_iso)).comments
        buckets: dict[str, list[CommentRecord]] = defaultdict(list)
        for comment in comments:
            buckets[comment.channel].append(comment)
        for channel in channels:
            try:
                await _sweep_channel(channel, buckets.get(channel, []))
            except Exception as exc:  # noqa: BLE001 - one channel must not abort the pass.
                await log_event(
                    "WARNING",
                    "neurocomment_sweep_channel_failed",
                    extra={"channel": channel, "error_type": type(exc).__name__},
                )
    await _reply_wait.review_waiting_posts(now)


async def _sweep_channel(channel: str, comments: list[CommentRecord]) -> None:
    """Re-read one channel's recent comments and record the ones that are gone.

    Recording is all it does. A channel whose moderators delete our comments is still
    commented on when its next post lands — deletions used to park it for an escalating
    1h→24h back-off, and that rule was removed by operator decision: the point of the
    fleet is to comment, and a lost comment costs nothing a pause would recover.
    """
    msg_ids = [c.comment_msg_id for c in comments if c.comment_msg_id is not None]
    if not msg_ids:
        return
    # Every comment author in turn, not ``comments[0]``: one kicked account used to fail
    # this read forever and only ever produce a warning. ``_sweep_read`` owns the walk, the
    # access-loss bookkeeping it hands to the re-join rule, and the one log line a channel
    # nobody could read is worth — so ``None`` here is already reported.
    result = await _sweep_read.read_alive(channel, comments, msg_ids)
    if result is None:
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
