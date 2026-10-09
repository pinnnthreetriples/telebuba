"""One page read per job, per mode — what it reads, what it records, what comes next.

* members: a page of the member list; the first page also learns whether the list is
  hidden. The walk ends at the operator's cap, at Telegram's own total, or at the first
  empty page — and an empty page before the total is "may be incomplete" (``partial``):
  ~10 000 is what Telegram is observed to hand out, not a documented limit.
* messages: a page of history (or of one forum topic), back to the operator's day floor.
* comments: a page of the channel's posts; every post with comments queues its comment
  pages, up to the per-post cap.

The source's admins are read once, after its first page answered (so an invite is already
joined by then), and only when "without the source's admins" is on. A failed admin read is
no failure of the source — a broadcast channel lists admins to admins only.

Failures are classified by the gateway's ``kind`` only: a flood or a dead connection ends
the page as retryable; anything else marks the source and counts against the account.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.logging import log_event
from core.telegram_client import TelegramReadError
from schemas.telegram_actions_user_parser import (
    HISTORY_PAGE_MAX,
    PARTICIPANTS_PAGE_MAX,
    ReadChannelPostReplies,
    ReadChannelPostRepliesResult,
    ReadChatHistoryAuthors,
    ReadChatHistoryAuthorsResult,
    ReadChatParticipants,
    ReadChatParticipantsResult,
)
from services.user_parser import _seams
from services.user_parser._access import reach, read_failure
from services.user_parser._filters import message_counts
from services.user_parser._streams import Job, JobResult

if TYPE_CHECKING:
    from pydantic import BaseModel

    from schemas.telegram_actions import TelegramReadAction
    from services.user_parser._context import RunContext, SourceState


class _UnexpectedResultError(TelegramReadError):
    def __init__(self) -> None:
        super().__init__("unexpected_result")


async def _read[T: BaseModel](account_id: str, action: TelegramReadAction, shape: type[T]) -> T:
    result = await _seams.execute_read(account_id, action)
    if not isinstance(result, shape):
        raise _UnexpectedResultError
    return result


def _admins_job(ctx: RunContext, job: Job) -> tuple[Job, ...]:
    source = ctx.sources[job.source]
    if not ctx.request.toggles.exclude_admins or source.admins_queued:
        return ()
    source.admins_queued = True
    return (Job(source=job.source, kind="admins"),)


async def _members(ctx: RunContext, account_id: str, job: Job, peer: str) -> JobResult:
    source = ctx.sources[job.source]
    cap = ctx.request.limits.members
    page = await _read(
        account_id,
        ReadChatParticipants(
            chat=peer, offset=job.offset, limit=min(PARTICIPANTS_PAGE_MAX, cap - job.offset)
        ),
        ReadChatParticipantsResult,
    )
    if page.total is not None:
        source.report.total = page.total
    if page.hidden:
        source.report.status = "hidden"
        await log_event(
            "WARNING",
            "user_parser_source_hidden",
            account_id=account_id,
            extra={"run_id": ctx.run_id, "source": source.raw},
        )
        return JobResult()
    for user in page.users:
        ctx.collector.member(user, job.source)
    walked = job.offset + len(page.users)
    source.seen = walked
    total = page.total if page.total is not None else source.report.total
    followups = _admins_job(ctx, job)
    if page.users and walked < cap and (total is None or walked < total):
        followups += (Job(source=job.source, kind="participants", offset=walked),)
    elif total is not None and walked < min(total, cap):
        source.partial = True
    return JobResult(followups=followups)


async def _admins(ctx: RunContext, account_id: str, _job: Job, peer: str) -> JobResult:
    try:
        page = await _read(
            account_id, ReadChatParticipants(chat=peer, admins=True), ReadChatParticipantsResult
        )
    except TelegramReadError as exc:
        return read_failure(exc) or JobResult()
    ctx.admin_ids.update(user.user_id for user in page.users)
    return JobResult()


def _record(
    ctx: RunContext,
    job: Job,
    page: ReadChatHistoryAuthorsResult | ReadChannelPostRepliesResult,
) -> None:
    authors = {user.user_id: user for user in page.users}
    for message in page.messages:
        author = authors.get(message.sender_id) if message.sender_id is not None else None
        if author is not None:
            counts = message_counts(message, ctx.request)
            ctx.collector.message(author, job.source, message.date, counts=counts)


async def _history(ctx: RunContext, account_id: str, job: Job, peer: str) -> JobResult:
    source = ctx.sources[job.source]
    cap = ctx.request.limits.messages
    page = await _read(
        account_id,
        ReadChatHistoryAuthors(
            chat=peer,
            offset_id=job.offset,
            limit=min(HISTORY_PAGE_MAX, cap - source.seen),
            min_date=ctx.min_date,
            top_msg_id=source.topic,
        ),
        ReadChatHistoryAuthorsResult,
    )
    source.seen += len(page.messages)
    _record(ctx, job, page)
    followups = _admins_job(ctx, job)
    if not page.done and page.messages and source.seen < cap:
        oldest = page.messages[-1].message_id
        followups += (Job(source=job.source, kind="history", offset=oldest),)
    return JobResult(followups=followups)


async def _posts(ctx: RunContext, account_id: str, job: Job, peer: str) -> JobResult:
    source = ctx.sources[job.source]
    cap = ctx.request.limits.posts
    page = await _read(
        account_id,
        ReadChatHistoryAuthors(
            chat=peer, offset_id=job.offset, limit=min(HISTORY_PAGE_MAX, cap - source.seen)
        ),
        ReadChatHistoryAuthorsResult,
    )
    source.seen += len(page.messages)
    followups = _admins_job(ctx, job) + tuple(
        Job(source=job.source, kind="replies", post_id=post.message_id)
        for post in page.messages
        if post.replies
    )
    if not page.done and page.messages and source.seen < cap:
        oldest = page.messages[-1].message_id
        followups += (Job(source=job.source, kind="posts", offset=oldest),)
    return JobResult(followups=followups)


async def _replies(ctx: RunContext, account_id: str, job: Job, peer: str) -> JobResult:
    source = ctx.sources[job.source]
    post_id = job.post_id or 0
    read = source.per_post.get(post_id, 0)
    limit = min(HISTORY_PAGE_MAX, ctx.request.limits.per_post - read)
    page = await _read(
        account_id,
        ReadChannelPostReplies(channel=peer, post_id=post_id, offset_id=job.offset, limit=limit),
        ReadChannelPostRepliesResult,
    )
    source.per_post[post_id] = read + len(page.messages)
    _record(ctx, job, page)
    if len(page.messages) == limit and read + limit < ctx.request.limits.per_post:
        oldest = page.messages[-1].message_id
        return JobResult(
            followups=(Job(source=job.source, kind="replies", post_id=post_id, offset=oldest),)
        )
    return JobResult()


_HANDLERS = {
    "participants": _members,
    "admins": _admins,
    "history": _history,
    "posts": _posts,
    "replies": _replies,
}


def _page_failed(source: SourceState, exc: TelegramReadError) -> JobResult:
    retry = read_failure(exc)
    if retry is not None:
        source.flooded = retry.flood_seconds is not None
        return retry
    source.failed = True
    return JobResult(failed=True)


async def handle(ctx: RunContext, account_id: str, job: Job) -> JobResult:
    """Read one page with ``account_id`` and record what it gave."""
    source = ctx.sources[job.source]
    if source.closed:
        return JobResult()
    peer = await reach(ctx, account_id, job.source)
    if isinstance(peer, JobResult):
        if peer.flood_seconds is not None:
            source.flooded = True
        return peer
    try:
        result = await _HANDLERS[job.kind](ctx, account_id, job, peer)
    except TelegramReadError as exc:
        return _page_failed(source, exc)
    if job.kind != "admins":
        source.flooded = False
    return result
