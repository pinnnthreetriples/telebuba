"""The wait that turns a won claim into a reply under a human's comment (#340).

In ``comment_mode='reply'`` the engine stops one step short of commenting: it wins the
post's claim as ``waiting`` and hands the post over here. This module is the other half —
the pass that revisits every parked post, reads its thread, and decides whether there is
now a stranger's comment worth answering, whether the wait has run out and we write first
after all, or whether the post has to be dropped — deleted meanwhile, too stale to be worth
a comment, or refused by a gate that shut while it waited (see :func:`_admit`).

The queue is the ``waiting`` rows themselves and the pass rides the deletion sweep, which
is what makes the wait survive a restart: a per-post task sleeping ten minutes loses every
parked post the moment the process ends, and nothing would ever resolve those rows again.
The only state here is the tick's clock and who it has already answered.

Telegram is reached through ``_seams`` like everywhere else in this domain. The generation
half is reached through ``engine`` on purpose (a late import — ``engine`` imports this
module for the park branch), so ``reply`` mode and ``first`` mode share one back half and
one patch seam rather than growing a second of each.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
from core.db import (
    fetch_active_campaign_for_channel,
    list_accounts,
    list_posted_comments_for_channel_since,
    list_waiting_comments,
    mark_comment_failed,
    park_comment,
    promote_waiting_to_claimed,
)
from core.logging import log_event
from schemas.telegram_actions import NewPostEvent, ReadPostComments
from schemas.telegram_actions_comments import ReadPostCommentsResult
from services.neurocomment import _gates, _seams
from services.neurocomment.settings_store import load_settings as load_neuro_settings

if TYPE_CHECKING:
    from schemas.neurocomment import CommentRecord, NeurocommentCampaign, NeurocommentSettings
    from schemas.telegram_actions_comments import PostCommentRecord

# Which of the thread's stranger comments we aim at, in the oldest-first list the gateway
# hands back. Never index 0: answering the comment that opened the discussion is the same
# "be first" reflex this whole mode exists to drop, and it reads as a bot camping the
# thread. Never past index 3 either — by the fifth comment a reply is buried under the
# ones after it, so the visibility we bought by waiting is spent.
_REPLY_TARGET_SLICE = slice(1, 4)

# Below this many strangers there is nothing to CHOOSE from, so the wait keeps running and
# only the deadline can resolve the post. At exactly two the slice above yields a single
# candidate, which is the honest answer rather than a special case.
_MIN_STRANGERS_TO_CHOOSE = 2

# How far past its own deadline a parked row may still be resolved. Ticks do get missed —
# this pass rides the deletion sweep, whose task exists only while the listener runs — so an
# hour of them is forgiven. Past that the row is dropped unsent: an operator who pressed Stop
# for a day and then Start would otherwise have the whole backlog commented in one pass, a
# day late, under posts nobody is reading — which is what a bot farm looks like. One constant
# tied to no other knob: "how stale may a comment be" is not what ``reply_wait_minutes`` asks.
_MAX_OVERDUE = timedelta(hours=1)


async def park_for_reply(
    event: NewPostEvent,
    campaign: NeurocommentCampaign,
    account_id: str,
    limits: NeurocommentSettings,
) -> bool:
    """Park this post for the wait instead of commenting now; ``True`` = caller must stop.

    ``True`` also covers LOSING the park race, because the caller's business with the post
    ends either way — the same shape ``claim_comment`` already gives the immediate path.

    The mode is refused outright while the deletion sweep is switched off
    (``deletion_sweep_interval_seconds`` = 0), because that sweep is the only thing that
    ever resolves a parked row. Without it the post would sit ``waiting`` forever, and
    ``_quota`` counts ``waiting`` — so the account would go on paying a quota slot for a
    comment nobody will ever send and quietly stop commenting altogether. Writing first is
    the lesser wrong; the WARNING is how the operator learns why the toggle they flipped is
    not taking effect, which is otherwise indistinguishable from the feature being broken.
    """
    if limits.comment_mode != "reply":
        return False
    if settings.neurocomment.deletion_sweep_interval_seconds <= 0:
        await log_event(
            "WARNING",
            "neurocomment_reply_mode_unavailable",
            account_id=account_id,
            extra={"channel": event.channel, "post_id": event.post_id},
        )
        return False
    if await park_comment(event.channel, event.post_id, campaign.campaign_id, account_id):
        await log_event(
            "INFO",
            "neurocomment_post_parked",
            account_id=account_id,
            extra={
                "channel": event.channel,
                "post_id": event.post_id,
                "wait_minutes": limits.reply_wait_minutes,
            },
        )
    return True


class _Promoted(NamedTuple):
    """Proof that a parked row is now OUR in-flight claim — the send's only ticket.

    A value of this type comes out of :func:`_promote` and nowhere else, and
    :func:`_reply_and_post` cannot run without one. That is the point: the
    ``waiting -> claimed`` UPDATE is what keeps two overlapping ticks — a slow pass still
    resolving rows when the next one starts, or the first pass after a restart re-reading a
    row an interrupted one had already picked up — from replying twice under one post, and a
    send reachable without it would re-open that window the first time somebody added a
    branch above it.

    ``mark_comment_posted`` is deliberately NOT tightened the same way. It is shared with
    the immediate path, and one new caller is no reason to change what a delivered comment
    means for every existing one.
    """

    row: CommentRecord


async def _promote(row: CommentRecord) -> _Promoted | None:
    """Take the parked row out of the wait; ``None`` when somebody else already has it."""
    if await promote_waiting_to_claimed(row.channel, row.post_id):
        return _Promoted(row)
    return None


class _Tick(NamedTuple):
    """What one pass reads once and judges every row of it against.

    ``answered`` is the one mutable field, per-pass on purpose: album items each fire their
    own post event while every comment on any of them lands in ONE thread, so two parked
    siblings resolved in the same tick would answer the same person twice under one visible
    post — two of our accounts in a row, the swarm this mode exists not to look like. Across
    ticks our own delivered ids already carry that memory (see :func:`_stranger_comments`).
    """

    now: datetime
    limits: NeurocommentSettings
    fleet: set[int]  # every user_id our accounts write under
    answered: set[int]  # senders this pass has already aimed a reply at


async def review_waiting_posts(now: datetime) -> None:
    """Resolve every parked post: reply to a stranger, write first, or keep waiting."""
    rows = (await list_waiting_comments()).comments
    if not rows:
        return
    # Read once per pass, not per row: ``reply_wait_minutes`` re-times every parked post at
    # once (the deadline is computed from ``created_at``, never stored), so one tick has to
    # judge every row against the same value — and the fleet's ids cannot change inside a tick.
    limits = await load_neuro_settings()
    tick = _Tick(
        now=now,
        limits=limits,
        fleet={a.user_id for a in (await list_accounts()).accounts if a.user_id is not None},
        answered=set(),
    )
    wait = timedelta(minutes=limits.reply_wait_minutes)
    for row in rows:
        try:
            deadline = datetime.fromisoformat(row.created_at) + wait
            await _resolve(row, tick, due=now >= deadline, stale=now > deadline + _MAX_OVERDUE)
        except Exception as exc:  # noqa: BLE001 - one parked post must not abort the pass.
            # The deletion pass's own per-channel code: this is a channel's step of the same
            # sweep failing, the operator already has that label, and the alternative was a
            # second word for one meaning.
            await log_event(
                "WARNING",
                "neurocomment_sweep_channel_failed",
                extra={"channel": row.channel, "error_type": type(exc).__name__},
            )


async def _admit(row: CommentRecord, tick: _Tick, *, stale: bool) -> NeurocommentCampaign | None:
    """The campaign to resume this parked post under, or ``None`` — row already abandoned.

    The immediate path's gates, re-asked through its own calls in its own order: age first
    because it is free, then the campaign, then everything under it
    (:func:`_gates.resume_refusal`, which carries why re-asking is not optional). Every
    refusal ABANDONS the row rather than leave it waiting for the gate to reopen — the post is
    long stale by the time any of them do, and ``_quota`` charges the account for every minute
    it sits ``waiting``. Age is reported as a skipped post rather than in a word of its own:
    from the operator's side that is what happened, and ``reason`` says which rule skipped it.
    """
    if stale:
        await _abandon(row, "neurocomment_post_skipped", reason="too_old")
        return None
    campaign = await fetch_active_campaign_for_channel(row.channel)
    if campaign is None:
        await _abandon(row, "neurocomment_no_campaign")
        return None
    refusal = await _gates.resume_refusal(campaign, row, tick.limits, tick.now)
    if refusal is None:
        return campaign
    await _abandon(row, refusal.event, reason=refusal.reason)
    return None


async def _resolve(row: CommentRecord, tick: _Tick, *, due: bool, stale: bool) -> None:
    """One parked post: read its thread and act on it, or leave it parked for the next tick."""
    campaign = await _admit(row, tick, stale=stale)
    if campaign is None:
        return
    read = await _read_thread(row)
    if read is not None and read.post_missing:
        await _abandon(row, "neurocomment_reply_post_gone")
        return
    if read is None:
        # The thread would not read. Before the deadline that is worth another tick; ON the
        # deadline the deadline wins — a channel we cannot read must not hold an account's
        # quota slot indefinitely — so we know nothing about the humans there and fall back
        # to what ``first`` mode would have done in the first place.
        if not due:
            return
        strangers: list[PostCommentRecord] = []
    else:
        strangers = await _stranger_comments(row, read.comments, tick)
    if len(strangers) >= _MIN_STRANGERS_TO_CHOOSE:
        target = _seams.rng.choice(strangers[_REPLY_TARGET_SLICE])
    elif due:
        # One stranger on the deadline still beats none: we are not the openers either way,
        # so answer them. None at all means the mode had nothing to work with and we write
        # first — the outcome ``neurocomment_reply_wait_expired`` counts, and the number an
        # operator turns ``reply_wait_minutes`` by.
        target = strangers[0] if strangers else None
    else:
        return  # nobody worth answering yet; the next tick asks again.
    promoted = await _promote(row)
    if promoted is None:
        return  # another tick took the row between the read and here — send nothing.
    if target is not None and target.sender_id is not None:
        tick.answered.add(target.sender_id)
    choice = _Choice(
        target,
        strangers.index(target) + 1 if target is not None else 0,
        len(strangers),
        unread=read is None,
    )
    await _reply_and_post(promoted, campaign, _rebuild_event(row, read), choice, tick.limits)


async def _read_thread(row: CommentRecord) -> ReadPostCommentsResult | None:
    """The parked post and its comment thread, or ``None`` when the read itself failed.

    Silent on failure by design: what the caller then does IS the record. Before the
    deadline the row stays parked and the next tick tries again, so a line here would repeat
    every five minutes for the whole wait; on the deadline the fallback logs its own. The
    deletion sweep already reports the channels our accounts cannot read
    (``_sweep_read.read_alive``), and it reports them once an hour rather than per row.
    """
    try:
        result = await _seams.execute_read(
            row.account_id,
            ReadPostComments(channel=row.channel, post_id=row.post_id),
        )
    except Exception:  # noqa: BLE001 - the caller decides what an unreadable thread means.
        return None
    # ``execute_read`` is typed to a bare ``BaseModel``, so the narrowing is the caller's
    # job (same as ``_sweep_read.read_alive``). Another type is the gateway breaking its
    # contract, which the next tick will not answer differently — so it reads as unreadable.
    return result if isinstance(result, ReadPostCommentsResult) else None


async def _stranger_comments(
    row: CommentRecord,
    comments: list[PostCommentRecord],
    tick: _Tick,
) -> list[PostCommentRecord]:
    """The thread's comments written by somebody outside our fleet, oldest first.

    Two filters, and the second is not redundant. ``user_id`` is the honest test — it is
    what Telegram stamps on the message — but an account whose row never got one back would
    have every comment it wrote counted as a stranger's, and the fleet would end up
    replying to itself and calling that a discussion. Our own delivered message ids close
    that gap: a comment we sent is on record here whatever the account row says.

    Channel-wide rather than per post because of albums: every item fires its own post
    event and every comment on any of them lands in ONE shared discussion thread, so a
    sibling post's comment shows up in this one's read.
    """
    # A day back. Album siblings are published within minutes of each other, so nothing
    # older can be in this thread, and a fixed window keeps this off the operator's sweep
    # knobs — where a retuned lookback would silently widen or empty it.
    since = (tick.now - timedelta(days=1)).isoformat()
    posted = await list_posted_comments_for_channel_since(row.campaign_id, row.channel, since)
    ours = {c.comment_msg_id for c in posted.comments if c.comment_msg_id is not None}
    return [
        comment
        for comment in comments
        # An unattributed message is not a stranger, it is an unknown. Telegram leaves the
        # sender off a message posted as the channel itself and off an anonymous admin's,
        # and both are the opposite of what this rule looks for: the point is to answer a
        # READER, so "we cannot say who wrote this" has to fall out with our own accounts
        # rather than count as a person and pull the whole wait forward.
        if comment.sender_id is not None
        and comment.sender_id not in tick.fleet
        # Answered by an earlier row of THIS pass — a sibling album item sharing the thread.
        and comment.sender_id not in tick.answered
        and comment.message_id not in ours
    ]


class _Choice(NamedTuple):
    """Whose comment the wait settled on, and where they sat among the strangers.

    ``target`` is ``None`` when the wait ran out with nobody to answer — the write-first
    fallback — and ``index`` is then 0. The positions ride along only to be logged: they are
    what shows an operator whether the "never the opener" rule landed where it was aimed,
    without needing the thread itself.
    """

    target: PostCommentRecord | None
    index: int  # 1-based position among the strangers, 0 when there is no target
    total: int
    # WHY there is no target, since the fallback has two causes that read alike from outside:
    # a thread we read and found no readers in, or one that would not read at all. One code
    # still counts "how often we wrote first"; this splits its reason, so an operator cutting
    # ``reply_wait_minutes`` over "nobody comments there" is not really looking at a channel
    # our accounts cannot read.
    unread: bool = False


def _rebuild_event(row: CommentRecord, read: ReadPostCommentsResult | None) -> NewPostEvent:
    """The post as the listener would have delivered it, rebuilt from the thread read.

    The database never stored the post, so this is the only source — ``post_media_kind``
    comes from the same classifier the listener applies, run by the gateway. An unreadable
    thread leaves nothing to rebuild from and the campaign prompt is then all the generator
    has; that is the price of resolving the row anyway rather than parking it forever.
    """
    return NewPostEvent(
        channel=row.channel,
        post_id=row.post_id,
        text=read.post_text if read is not None else "",
        media_kind=read.post_media_kind if read is not None and read.post_media_kind else "none",
    )


async def _reply_and_post(
    promoted: _Promoted,
    campaign: NeurocommentCampaign,
    event: NewPostEvent,
    choice: _Choice,
    limits: NeurocommentSettings,
) -> None:
    """Record the decision and hand the send to the pipeline's normal back half.

    Takes ``promoted`` rather than a row so it cannot be reached without the
    ``waiting -> claimed`` transition (see :class:`_Promoted`).
    """
    row = promoted.row
    if choice.target is None:
        await log_event(
            "INFO",
            "neurocomment_reply_wait_expired",
            account_id=row.account_id,
            extra={
                "channel": row.channel,
                "post_id": row.post_id,
                "waited_minutes": limits.reply_wait_minutes,
                "reason": "thread_unread" if choice.unread else "no_readers",
            },
        )
    else:
        await log_event(
            "INFO",
            "neurocomment_reply_to_human",
            account_id=row.account_id,
            extra={
                "channel": row.channel,
                "post_id": row.post_id,
                "stranger_index": choice.index,
                "stranger_count": choice.total,
            },
        )
    # Late import: ``engine`` imports this module for the park branch, so a top-level import
    # cycles — and ``engine._generate_and_post`` is the seam the generation tests already
    # patch, so reaching the back half through it keeps one path rather than two.
    from services.neurocomment import engine  # noqa: PLC0415

    try:
        await engine._generate_and_post(  # noqa: SLF001 - this domain's own back half.
            event,
            campaign,
            row.account_id,
            limits,
            target=choice.target,
        )
    except BaseException:
        # The immediate path's rule, for its reason: any exit other than a delivered
        # comment must not leave the row ``claimed``, or the post is never commentable
        # again and the account keeps paying a quota slot for it.
        await mark_comment_failed(row.channel, row.post_id)
        raise


async def _abandon(row: CommentRecord, event: str, *, reason: str | None = None) -> None:
    """Fail a parked row for good and say why, in the immediate path's own words.

    Every caller reports a state the engine already has a code for, so none coins one: an
    operator must not have to learn a second phrase for «канал на паузе» because the post
    happened to be parked. Silent when ``_give_up`` loses the row — the tick that took it
    owns the outcome and logs its own.
    """
    extra: dict[str, object] = {"channel": row.channel, "post_id": row.post_id}
    if reason is not None:
        extra["reason"] = reason
    if await _give_up(row):
        await log_event("INFO", event, account_id=row.account_id, extra=extra)


async def _give_up(row: CommentRecord) -> bool:
    """Fail a parked row for good; ``False`` when somebody else took it first.

    ``failed`` rather than ``release_claim``'s DELETE, for the reason
    ``_sweep._reclaim_stale_claims`` spells out: the row is also the idempotency gate that
    ``claim_comment`` wins, and dropping it hands the post back to the next delivery of the
    same event. ``failed`` is terminal and costs the account nothing, since ``_quota``
    counts only ``waiting``/``claimed``/``posted``.
    """
    if await _promote(row) is None:
        return False
    await mark_comment_failed(row.channel, row.post_id)
    return True
