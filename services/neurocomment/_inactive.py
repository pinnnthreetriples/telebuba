"""The rule for a channel that has stopped publishing.

A campaign slot spent on a channel that posts nothing is a slot that comments nothing, and
it lies to the operator twice: the board counts a channel that will never produce a post,
and every account carries a subscription it never uses. After
``inactive_channel_drop_days`` of silence the channel leaves its campaign and its comment
authors walk out of its discussion group.

The whole rule turns on ONE distinction, and every guard below is a face of it. Our own
records say only that WE saw nothing, which is equally what a week of downtime looks like
(this app restarts every day or two) and what a subscription that silently stopped
delivering looks like — the listener's documented blind spot: a public channel that kicked
us keeps resolving, so the loss arrives as silence, never as an error. So our silence only
nominates a suspect; the verdict comes from asking Telegram when the channel last
published, and ONLY a dated post older than the cutoff is a verdict. Anything else —
a failed read, an empty answer, a message with no date — is an unknown, and an unknown
keeps the channel. The drop deletes per-account channel pins that nothing restores
(``_comments_off`` exists because of that), so evidence has to point AT the drop; absence
of evidence must never reach it.

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
from schemas.telegram_actions import LeaveDiscussionGroup
from schemas.telegram_actions_activity import GetLastPostAt, LastPostResult
from services.neurocomment import _seams
from services.neurocomment._pins import serving_accounts
from services.neurocomment._sweep_read import _may_be_in_chat

if TYPE_CHECKING:
    from schemas.neurocomment import CampaignChannelLink, NeurocommentReadiness

# Suspects judged per tick. The migration leaves ``last_post_at`` NULL on purpose, so on
# the first tick after the upgrade EVERY link is a suspect at once; unbounded, that is one
# Telegram read per channel in a single burst on the one account the whole runtime depends
# on — the shape ``ban_check_concurrency`` and the paced joins exist to avoid. The rest
# come round on later ticks, oldest link first.
_PER_TICK = 5
# A suspect is probed at most this often, however many ticks pass. Without it a channel
# whose read always fails — a deleted channel, a private one, a `+HASH` the listener cannot
# resolve — is re-read every 5 minutes for the life of the process: 288 RPCs and 288 log
# rows a day, for ever, on a pair nothing can decide. Same shape and the same reason as
# ``_sweep_read``'s read mute, and in memory for the same reason: losing it on a restart
# costs one extra read.
_PROBE_EVERY = timedelta(hours=1)
_PROBED_AT: dict[str, datetime] = {}


def reset_probe_clock() -> None:
    """Forget which channels were probed recently (tests; also on a listener switch)."""
    _PROBED_AT.clear()


async def review_silent_channels(now: datetime) -> None:
    """Retire every watched channel Telegram confirms has gone quiet. Rides the sweep tick.

    Reads nothing from Telegram in the common case: the suspect list is one query, and a
    fleet whose channels all post lately produces no suspects and no RPCs at all.

    The listener is the account that probes, because it is the one joined to the CHANNEL —
    the comment authors are only in its discussion group, which can be perfectly alive
    while the channel above it is dead. No listener means no campaign is running; the
    suspects keep until one is.
    """
    days = settings.neurocomment.inactive_channel_drop_days
    if days <= 0:
        return
    cutoff = now - timedelta(days=days)
    links = (await list_silent_watch_channels(cutoff.isoformat(), _PER_TICK)).links
    due = [link for link in links if _probe_due(link.channel, now)]
    if not due:
        return
    listener = await get_listener_account_id()
    if listener is None:
        return
    for link in due:
        _PROBED_AT[link.channel] = now
        await _judge_channel(link, listener, cutoff)


def _probe_due(channel: str, now: datetime) -> bool:
    probed_at = _PROBED_AT.get(channel)
    return probed_at is None or now - probed_at >= _PROBE_EVERY


async def _judge_channel(link: CampaignChannelLink, listener: str, cutoff: datetime) -> None:
    """Ask Telegram when ``link.channel`` last published, then act on the answer.

    Only ONE answer retires the channel: a post whose date is older than the cutoff. The
    other three outcomes all keep it, and for the same reason — they say we could not tell,
    not that there was nothing to tell.

    * The read raised. Flood wait, a channel the listener was kicked from, a session fault.
    * The read came back empty. Telethon returns an empty list without raising for a
      channel it can see but not read from — it silently skips ``MessageEmpty``, and warns
      in its own source that some channels "return less messages than requested" for
      content excluded by local law. With ``limit=1`` that is an empty answer about a live
      channel, and reading it as "never published" would drop exactly the channels
      Telegram is being awkward about.
    * The newest message carries no date.

    All three log ``neurocomment_channel_activity_unknown`` and leave the channel alone.
    The cost of waiting is one dead channel on the board for another hour; the cost of
    guessing is a live channel unlinked, its per-account pins deleted, and its accounts
    walked out of a chat they will have to spend the rolling-24h join cap to re-enter.
    """
    try:
        result = await _seams.execute_read(listener, GetLastPostAt(channel=link.channel))
        if not isinstance(result, LastPostResult):
            # The typed gateway breaking its contract. Same verdict as a raised error.
            await _log_unknown(link, listener, type(result).__name__)
            return
        if result.last_post_at is None:
            await _log_unknown(link, listener, "no_dated_message")
            return
        last_post_at = datetime.fromisoformat(result.last_post_at)
    except Exception as exc:  # noqa: BLE001 - a failed probe must never abort the sweep.
        # The parse is inside the try with the read it parses: one unexpected shape must
        # cost this channel its tick, never the suspects queued behind it.
        await _log_unknown(link, listener, type(exc).__name__)
        return
    if last_post_at > cutoff:
        # The channel is alive and we simply never saw it. Repairing the stamp is what
        # stops this from re-probing; the WARNING is the point of the branch, because a
        # subscription that stopped delivering has no other symptom.
        await stamp_channel_post_seen(link.channel, result.last_post_at)
        await log_event(
            "WARNING",
            "neurocomment_channel_posts_missed",
            extra={"channel": link.channel, "last_post_at": result.last_post_at},
        )
        return
    await _drop_and_leave(link, result.last_post_at)


async def _log_unknown(link: CampaignChannelLink, listener: str, error_type: str) -> None:
    await log_event(
        "WARNING",
        "neurocomment_channel_activity_unknown",
        account_id=listener,
        extra={"channel": link.channel, "error_type": error_type},
    )


async def _drop_and_leave(link: CampaignChannelLink, last_post_at: str) -> None:
    """Unlink the dead channel, then walk its comment authors out of the discussion group.

    Who serves the channel is read BEFORE the unlink, not after: ``deactivate_channel``
    also DELETES every per-account pin for it, and ``serving_accounts`` keeps a pinned
    account only while the channel is still in its subset. Read afterwards, every pinned
    account reads as not-serving and is left sitting in the group of a channel we have just
    written off — silently, and only for the pinned ones. ``_channel_pause`` reads its
    coverage before its own unlink for the same reason.

    Unlink FIRST and leave best-effort after, the order every sibling verdict in this
    domain uses (``bans._mark_banned_and_leave``, ``_captcha_retry._give_up_and_leave``):
    the verdict is the durable half and must survive a leave RPC that dies.

    The LISTENER does not leave the channel. Doing that honestly means stamping the
    standing join as lost, and that stamp means "Telegram proved this account is out" and
    spends the listener's re-join budget — so a channel the operator re-links later could
    arrive already exhausted. One account keeping a dead subscription is the cheaper of
    the two mistakes, and the unlink already stops the listener watching it.
    """
    rows = await _serving_rows(link)
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
            "last_post_at": last_post_at,
        },
    )
    await _walk_out(link.channel, rows)


async def _serving_rows(link: CampaignChannelLink) -> list[NeurocommentReadiness]:
    links = (await list_campaign_accounts(link.campaign_id)).links
    serving = serving_accounts(links, link.channel)
    return (await list_channel_readiness(link.campaign_id, link.channel, serving)).readiness


async def _walk_out(channel: str, rows: list[NeurocommentReadiness]) -> None:
    """Leave the discussion group with every author our own records still place inside it.

    The rule ``_give_up`` was rewritten around: a leave sent for a chat we are not in
    spends two RPCs to be told so. Membership normally costs nothing and this domain never
    leaves over a refusal — but the channel is gone from the campaign now, so the
    subscription buys nothing either, and an account carrying dozens of dead channels is
    exactly the shape anti-ban work avoids.

    Jittered between accounts, like every other multi-account burst here (the paced joins,
    the @SpamBot probes): each leave is two RPCs (resolve the linked group, then leave it),
    and six co-proxied accounts exiting one group inside a second is the join-burst
    signature in reverse.
    """
    for index, row in enumerate(rows):
        if not (row.joined and _may_be_in_chat(row)):
            continue
        if index:
            await _seams.sleep(_seams.rng.uniform(1.0, 3.0))
        with suppress(Exception):
            # The gateway logs the RPC either way, so a swallowed failure is not silent.
            await _seams.execute(row.account_id, LeaveDiscussionGroup(channel=channel))
