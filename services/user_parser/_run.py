"""One run, start to settle: read every source, filter the people, save them as a base.

The run ends when the queue is empty or no account is left. Whatever way it ends — done,
every account gone (``failed``), Stop (``stopped``), the server shutting down
(``interrupted``) — the people collected so far are filtered and saved: a base is never
lost because the run did not finish.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event, signal_event
from core.repositories import user_parser as repository
from core.repositories.accounts import list_account_user_ids
from services.user_parser import _seams, _state
from services.user_parser._filters import FilterContext, apply_filters
from services.user_parser._jobs import handle
from services.user_parser._pool import release_accounts
from services.user_parser._streams import Hooks, Job, JobResult, Streams

if TYPE_CHECKING:
    from schemas.user_parser_run import UserParserRunStatus
    from services.user_parser._context import RunContext, SourceState
    from services.user_parser._streams import JobKind, StopReason

logger = logging.getLogger(__name__)

PROGRESS_SIGNAL = "user_parser_progress"
_FIRST_JOB: dict[str, JobKind] = {
    "members": "participants",
    "messages": "history",
    "comments": "posts",
}
_SETTLED_EVENTS: dict[UserParserRunStatus, str] = {
    "done": "user_parser_run_finished",
    "stopped": "user_parser_run_stopped",
    "failed": "user_parser_run_failed",
}


def pause_seconds(ctx: RunContext, *, new_source: bool) -> float:
    """The pause before an account's next read: per source, or between two pages.

    "Fast" shortens both, never below the configured floors; "protect" jitters them by up
    to the configured share either way.
    """
    request, limits = ctx.request, settings.user_parser
    if new_source:
        delay, floor = request.chat_delay, limits.min_chat_delay_seconds
    else:
        delay, floor = request.request_delay, limits.min_request_delay_seconds
    if request.fast:
        delay *= limits.fast_factor
    if request.protect:
        delay *= _seams.rng.uniform(1 - limits.jitter, 1 + limits.jitter)
    return max(delay, floor)


def _settled_status(source: SourceState, count: int) -> None:
    report = source.report
    if source.closed:
        return
    if source.failed:
        report.status = "partial" if count else "failed"
    elif source.partial:
        report.status = "partial"
    elif source.flooded:
        report.status = "flood"
    else:
        report.status = "ok"


async def _source_done(ctx: RunContext, index: int) -> None:
    source = ctx.sources[index]
    source.report.count = ctx.collector.source_count(index)
    source.report.finished_at = _seams.now().isoformat()
    _settled_status(source, source.report.count)
    ctx.live.sources_done += 1
    if source.report.status == "partial" and not source.failed:
        await log_event(
            "WARNING",
            "user_parser_source_partial",
            extra={
                "run_id": ctx.run_id,
                "source": source.raw,
                "collected": source.report.count,
                "total": source.report.total,
            },
        )


async def _flood(ctx: RunContext, account_id: str, seconds: int) -> None:
    await _seams.set_cooldown(account_id, _seams.now() + timedelta(seconds=seconds))
    await log_event(
        "WARNING",
        "user_parser_account_flood",
        account_id=account_id,
        extra={"run_id": ctx.run_id, "seconds": seconds},
    )


def _page_done(ctx: RunContext) -> None:
    for index, source in enumerate(ctx.sources):
        source.report.count = ctx.collector.source_count(index)
    ctx.live.collected_raw = ctx.collector.raw
    signal_event(PROGRESS_SIGNAL, {"run_id": ctx.run_id})


async def _collect(ctx: RunContext) -> StopReason | None:
    async def _handle(account_id: str, job: Job) -> JobResult:
        return await handle(ctx, account_id, job)

    async def _on_flood(account_id: str, seconds: int) -> None:
        await _flood(ctx, account_id, seconds)

    async def _on_source_done(index: int) -> None:
        await _source_done(ctx, index)

    streams = Streams(
        ctx.live.accounts,
        _handle,
        lambda new_source: pause_seconds(ctx, new_source=new_source),
        Hooks(
            on_flood=_on_flood,
            on_source_done=_on_source_done,
            on_page=lambda: _page_done(ctx),
        ),
    )
    kind = _FIRST_JOB[ctx.request.mode]
    stop = await streams.run(Job(source=index, kind=kind) for index in range(len(ctx.sources)))
    for index in sorted(streams.unfinished_sources()):
        source = ctx.sources[index]
        source.report.count = ctx.collector.source_count(index)
        source.report.status = "flood" if stop == "flooded" else "failed"
    return stop


async def _filter_context(ctx: RunContext) -> FilterContext:
    own = await list_account_user_ids()
    collected: set[int] = set()
    if ctx.request.toggles.exclude_collected and ctx.collector.people:
        collected = await repository.collected_user_ids(
            ctx.collector.people, exclude_run_id=ctx.run_id
        )
    return FilterContext(
        request=ctx.request,
        own_ids=own,
        admin_ids=frozenset(ctx.admin_ids),
        collected_ids=frozenset(collected),
    )


async def _settle(ctx: RunContext, status: UserParserRunStatus, reason: str | None) -> None:
    live = ctx.live
    for index, source in enumerate(ctx.sources):
        source.report.count = ctx.collector.source_count(index)
        if source.report.status == "pending" and source.report.count:
            source.report.status = "partial"
    kept, dropped = apply_filters(ctx.collector.people.values(), await _filter_context(ctx))
    kept.sort(key=lambda agg: (-agg.message_count, agg.user.user_id))
    live.status = status
    live.stop_reason = reason
    live.finished_at = _seams.now().isoformat()
    live.collected_raw = ctx.collector.raw
    live.kept = len(kept)
    live.filtered = dropped
    await repository.settle_run(live, [agg.record() for agg in kept])
    event = _SETTLED_EVENTS.get(status)
    if event is not None:
        await log_event(
            "ERROR" if status == "failed" else "INFO",
            event,
            extra={
                "run_id": ctx.run_id,
                "collected": live.collected_raw,
                "kept": live.kept,
                "reason": reason,
            },
        )
    signal_event(PROGRESS_SIGNAL, {"run_id": ctx.run_id})


async def run(ctx: RunContext, account_ids: list[str]) -> None:
    """The run's task. Always settles, always gives its accounts back."""
    try:
        try:
            stop = await _collect(ctx)
        except asyncio.CancelledError:
            entry = _state.live(ctx.run_id)
            stopped = entry is not None and entry.stop_requested
            await _settle(ctx, "stopped" if stopped else "interrupted", None)
            raise
        except Exception as exc:
            logger.exception("user parser run %s failed", ctx.run_id)
            await _settle(ctx, "failed", type(exc).__name__)
            return
        await _settle(ctx, "failed" if stop is not None else "done", stop)
    finally:
        release_accounts(ctx.run_id, account_ids)
