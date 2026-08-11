"""The rule for a channel that has stopped publishing.

A campaign slot spent on a channel that posts nothing is a slot that comments nothing, and
it lies to the operator twice: the board counts a channel that will never produce a post,
and every account carries a subscription it never uses. After
``inactive_channel_drop_days`` of silence the channel leaves its campaign and everyone
walks out of it.

The whole rule turns on ONE distinction. Our own records say only that WE saw nothing,
which is equally what a week of downtime looks like (this app restarts every day or two)
and what a subscription that silently stopped delivering looks like — the listener's
documented blind spot: a public channel that kicked us keeps resolving, so the loss
arrives as silence, never as an error. So our silence only nominates a suspect; the
verdict comes from asking Telegram when the channel last published. One read per suspect,
and a channel Telegram says is alive gets its stamp repaired instead of being dropped —
which is also the only signal that would ever name that blind spot out loud.

Its own module because ``_sweep`` is at the file-size cap and because this is a distinct
concern: the sibling drop rules (``_channel_pause``, ``_rejoin``, ``_captcha_retry``,
``bans``) all judge whether the channel will let US act, while this one judges whether the
channel does anything at all — a verdict none of them can reach, since a silent channel
produces no failure for them to count.
"""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    get_listener_account_id,
    list_campaign_accounts,
    list_channel_readiness,
    list_silent_watch_channels,
    stamp_channel_post_seen,
)
from core.logging import log_event
from schemas.telegram_actions import LeaveChannel, LeaveDiscussionGroup
from schemas.telegram_actions_activity import GetLastPostAt, LastPostResult
from services.neurocomment import _seams
from services.neurocomment._pins import serving_accounts
from services.neurocomment._sweep_read import _may_be_in_chat

if TYPE_CHECKING:
    from schemas.neurocomment import CampaignChannelLink


async def review_silent_channels(now: datetime) -> None:
    """Drop every watched channel Telegram confirms has gone quiet. Rides the sweep tick.

    Reads nothing from Telegram in the common case: the suspect list is one indexed query,
    and a fleet whose channels all post lately produces no suspects and no RPCs at all.

    The listener is the account that probes, because it is the one joined to the CHANNEL —
    the comment authors are only in its discussion group, which can be perfectly alive
    while the channel above it is dead. No listener means no campaign is running; the
    suspects keep until one is.
    """
    days = settings.neurocomment.inactive_channel_drop_days
    if days <= 0:
        return
    cutoff = now - timedelta(days=days)
    links = (await list_silent_watch_channels(cutoff.isoformat())).links
    if not links:
        return
    listener = await get_listener_account_id()
    if listener is None:
        return
    for link in links:
        await _judge_channel(link, listener, cutoff)


async def _judge_channel(link: CampaignChannelLink, listener: str, cutoff: datetime) -> None:
    """Ask Telegram when ``link.channel`` last published, then act on the answer.

    A read that fails decides nothing and is retried on the next tick — the channel stays
    a suspect, which is the safe direction: the cost of waiting is one dead channel on the
    board for five more minutes, and the cost of guessing is a live channel unlinked and
    seven accounts walked out of it over a flood wait.

    The failure is logged rather than swallowed because this read is the rule's only
    evidence: a probe that fails every tick would otherwise leave the channel suspect
    forever with nothing saying why.
    """
    try:
        result = await _seams.execute_read(listener, GetLastPostAt(channel=link.channel))
    except Exception as exc:  # noqa: BLE001 - a failed probe must never abort the sweep.
        await _log_probe_failed(link, listener, type(exc).__name__)
        return
    if not isinstance(result, LastPostResult):
        # The typed gateway breaking its contract; same verdict as a raised error — we
        # learned nothing, so we decide nothing.
        await _log_probe_failed(link, listener, type(result).__name__)
        return
    if result.last_post_at is not None and datetime.fromisoformat(result.last_post_at) > cutoff:
        # The channel is alive and we simply never saw it. Repairing the stamp is what
        # stops this from re-probing every five minutes; the WARNING is the point of the
        # branch, because a subscription that stopped delivering has no other symptom.
        await stamp_channel_post_seen(link.channel, result.last_post_at)
        await log_event(
            "WARNING",
            "neurocomment_channel_posts_missed",
            extra={"channel": link.channel, "last_post_at": result.last_post_at},
        )
        return
    await _drop_and_leave(link, listener, result.last_post_at)


async def _log_probe_failed(link: CampaignChannelLink, listener: str, error_type: str) -> None:
    await log_event(
        "WARNING",
        "neurocomment_channel_activity_unknown",
        account_id=listener,
        extra={"channel": link.channel, "error_type": error_type},
    )


async def _drop_and_leave(
    link: CampaignChannelLink,
    listener: str,
    last_post_at: str | None,
) -> None:
    """Unlink the dead channel, then walk everyone out of it.

    Unlink FIRST and leave best-effort after, the order every sibling verdict in this
    domain uses (``bans._mark_banned_and_leave``, ``_captcha_retry._give_up_and_leave``):
    the verdict is the durable half and must survive a leave RPC that dies. A failed leave
    costs a silent membership in a channel we no longer read — the cheap direction.
    """
    # Late import: ``campaigns`` reaches back here through _runtime -> engine -> sweep.
    from services.neurocomment import campaigns as campaigns_service  # noqa: PLC0415

    # Via the service, not the repository, so the listener reconciles and stops watching
    # the channel — exactly like the four sibling drop rules.
    await campaigns_service.deactivate_channel(link.campaign_id, link.channel)
    # Its own event and not ``neurocomment_channel_dropped`` with a reason: the sibling
    # causes each have one (``_channel_captcha_unsolved``, ``_comments_off_dropped``), and
    # the operator's feed explains an event by name — the write-blocked drop's own text
    # says "nobody left the chat", which is the opposite of what happens here.
    await log_event(
        "WARNING",
        "neurocomment_channel_inactive_dropped",
        extra={
            "channel": link.channel,
            "campaign_id": link.campaign_id,
            # "never" and not an empty string: a channel that has never published a single
            # message is a different mistake from one that went quiet, and the operator
            # reading the feed is the one who has to tell them apart.
            "last_post_at": last_post_at or "never",
        },
    )
    await _walk_everyone_out(link, listener)


async def _walk_everyone_out(link: CampaignChannelLink, listener: str) -> None:
    """Leave the discussion group with every author still in it, then the channel itself.

    Only pairs our own records still place inside the chat, the rule ``_give_up`` was
    rewritten around: a leave sent for a chat we are not in spends two RPCs to be told so.
    Membership normally costs nothing and this domain never leaves over a refusal — but the
    channel is gone from the campaign now, so the subscription buys nothing either, and an
    account carrying dozens of dead channels is exactly the shape anti-ban work avoids.
    """
    links = (await list_campaign_accounts(link.campaign_id)).links
    serving = serving_accounts(links, link.channel)
    rows = (await list_channel_readiness(link.campaign_id, link.channel, serving)).readiness
    for row in rows:
        if row.joined and _may_be_in_chat(row):
            await _leave(row.account_id, LeaveDiscussionGroup(channel=link.channel))
    await _leave(listener, LeaveChannel(channel=link.channel))


async def _leave(account_id: str, action: LeaveChannel | LeaveDiscussionGroup) -> None:
    """One best-effort leave. Never raises: the drop has already been decided and logged."""
    # The gateway logs the RPC either way, so a swallowed failure is not a silent one.
    with suppress(Exception):
        await _seams.execute(account_id, action)
