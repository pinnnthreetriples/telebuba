"""The rule for an account kicked out of a discussion group: get itself back in.

A pair that loses chat access is parked with onboarding's hard-join-failure sentinel
``(joined=False, captcha_passed=True, ready=False)`` — by the post path on
``ChannelPrivateError`` and by ``_classify`` on a hard join failure. Both mean the same
thing: this pair needs a fresh join. Nothing retried it, because onboarding has NO timer
(operator Start, boot, and campaign reconciles only), so a kicked account waited for a
human — and a channel whose every account was kicked produced nothing at all, silently.

The rule, deliberately shaped like the two already here (``_sweep._review_join_requests``
for approval-gated joins, ``_channel_pause`` for write-blocked channels): one re-join per
``channel_pause_hours``, at most ``channel_max_rounds`` of them, then the channel leaves
its campaign. It rides the 5-minute deletion sweep — the only periodic neurocomment task
— and pokes onboarding rather than joining itself: the sweep must spend no join RPCs, and
onboarding already owns the join cap and the jitter.

Counter and deadline are persisted per pair (migration #43) rather than kept in memory,
for the reason ``_channel_pause`` documents: the live app restarts, and a four-day rule
built on module dicts never reaches day four.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    fetch_active_campaigns_for_channels,
    list_access_lost_readiness,
    list_campaign_accounts,
    list_channel_readiness,
)
from core.logging import log_event
from services.neurocomment._pins import serving_accounts

if TYPE_CHECKING:
    from schemas.neurocomment import NeurocommentReadiness


def access_lost(readiness: NeurocommentReadiness) -> bool:
    """True for the hard-join-failure sentinel — the pair is out and wants back in.

    No other path writes ``captcha_passed`` on an unjoined row, which is what makes the
    sentinel readable. Field for field the SQL predicate in ``_readiness._ACCESS_LOST``,
    including the two exclusions: onboarding refuses to re-join a skipped (#148) or banned
    (#30) pair, so counting one as parked would leave a pair that is due forever — and the
    review would poke onboarding every five minutes for a join that never happens.
    """
    return (
        not readiness.joined
        and readiness.captcha_passed
        and not readiness.ready
        and not readiness.human_skipped
        and not readiness.banned
    )


def _exhausted(readiness: NeurocommentReadiness) -> bool:
    """True once this pair has used every re-join it will ever get."""
    return readiness.rejoin_attempts >= settings.neurocomment.channel_max_rounds


def retry_due(readiness: NeurocommentReadiness, now: datetime) -> bool:
    """True when this pair may spend a re-join right now.

    Never stamped = due immediately: the first retry happens on the sweep tick after the
    kick (~5 minutes), which is what makes a transient access loss — Telethon also raises
    ``ChannelPrivateError`` on a stale cached entity — cost minutes instead of a day. Each
    later attempt waits the full window, and the fourth one is the last.
    """
    if _exhausted(readiness):
        return False
    if readiness.rejoin_attempted_at is None:
        return True
    attempted = datetime.fromisoformat(readiness.rejoin_attempted_at)
    return now - attempted >= timedelta(hours=settings.neurocomment.channel_pause_hours)


async def review_access_lost(now: datetime) -> None:
    """Age the parked pairs: poke onboarding for the due ones, drop the dead channels.

    Never raises — a failure here must not abort the deletion sweep that owns this tick.
    """
    try:
        rows = (await list_access_lost_readiness()).readiness
    except Exception as exc:  # noqa: BLE001 - the review must never abort the sweep loop.
        await log_event(
            "WARNING",
            "neurocomment_rejoin_review_failed",
            extra={"error_type": type(exc).__name__},
        )
        return
    by_channel: dict[str, list[NeurocommentReadiness]] = defaultdict(list)
    for row in rows:
        by_channel[row.channel].append(row)
    # Only channels still linked to an ACTIVE campaign can be dropped; the bulk read also
    # hands us the campaign id ``deactivate_channel`` needs.
    campaigns = await fetch_active_campaigns_for_channels(list(by_channel))
    retry_due_somewhere = False
    for channel, channel_rows in by_channel.items():
        campaign = campaigns.get(channel)
        if campaign is None:
            continue
        parked = [row for row in channel_rows if access_lost(row)]
        if any(retry_due(row, now) for row in parked):
            retry_due_somewhere = True
            continue
        # Unlike the join-request review, the retry above is NOT gated on the channel
        # being otherwise dead: a kicked account must get back into a chat the other five
        # comment in fine. Only the DROP is, and that check lives one call down — the
        # single place that decides whether anything still works here.
        if not all(_exhausted(row) for row in parked):
            # Nothing due, but somebody still has attempts left: they are only waiting out
            # a window, and a channel must never be dropped mid-timeline.
            continue
        await _drop_channel_if_nothing_works(campaign.campaign_id, channel, len(parked))
    if retry_due_somewhere:
        # Late import: ``_runtime`` reaches this module through the sweep, so a top-level
        # import cycles. Same poke ``_sweep._review_join_requests`` uses — onboarding, not
        # a join RPC from here, does the joining.
        from services.neurocomment import _runtime, _signals  # noqa: PLC0415

        _runtime._ensure_onboarding_running(_signals.signal_onboarding_progress)  # noqa: SLF001


async def _drop_channel_if_nothing_works(campaign_id: str, channel: str, parked: int) -> None:
    """Unlink a channel every serving account has run out of re-joins for.

    The coverage rule of ``bans._unlink_channel_if_no_account_left``, and for its reason:
    a serving account with NO readiness row was never tried here, not tried and failed,
    and onboarding reaches a fleet slowly. So every serving account must have a row, and
    any usable one keeps the channel.
    """
    links = (await list_campaign_accounts(campaign_id)).links
    serving = serving_accounts(links, channel)
    rows = (await list_channel_readiness(campaign_id, channel, serving)).readiness
    if len(rows) != len(serving) or any(row.ready for row in rows):
        return
    # Via the service, not the repository, so the listener reconciles and stops watching
    # the channel — exactly like the two sibling rules.
    from services.neurocomment import campaigns as campaigns_service  # noqa: PLC0415

    await campaigns_service.deactivate_channel(campaign_id, channel)
    await log_event(
        "WARNING",
        "neurocomment_channel_rejoin_exhausted",
        extra={
            "channel": channel,
            "campaign_id": campaign_id,
            "parked_accounts": parked,
            "attempts": settings.neurocomment.channel_max_rounds,
            "reason": "rejoin_exhausted",
        },
    )
