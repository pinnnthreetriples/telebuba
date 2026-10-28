"""One run: lay the chats out, play rounds with one task per account, rest between them.

A round ends when none of its chats is still open. Between rounds every chat rests;
the next round re-queues the ones that finished and keeps the ones that were skipped
for good — and since every account is already inside its chats, round 2 onward has no
joins and no pause after one, only the pauses between messages and chats.

An account Telegram or its own limit stops RESTS (``_rotation``): it leaves the
rotation for a while, a waker task sits the rest out and starts its worker again. An
account that dies (frozen, logged out, errors in a row) hands its open chats to the
others at once. Only with nobody live and nobody resting does the run end ``stalled``,
the chats waiting for an account the operator adds before pressing Continue.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Literal

from core.config import settings
from core.logging import log_event
from core.repositories import chat_broadcast as repository
from services.chat_broadcast import _moves, _rotation, _seams, _send, _state, _worker, targets
from services.chat_broadcast._context import load_context

if TYPE_CHECKING:
    from schemas.chat_broadcast import MinutesRange
    from services.chat_broadcast._context import RunContext

RunOutcome = Literal["done", "stalled"]
RoundOutcome = Literal["round_done", "stalled", "volume"]


async def run_campaign(campaign_id: str, run_id: str, *, refresh: bool) -> RunOutcome:
    ctx = await load_context(campaign_id, run_id)
    record = await repository.fetch_campaign(campaign_id)
    if ctx is None or record is None:
        return "done"
    live = await live_accounts(campaign_id)
    if record.round == 0:
        previous = await repository.list_targets(campaign_id)
        seeds = await targets.materialize(campaign_id, ctx.settings, live)
        await repository.replace_targets(
            campaign_id, targets.carry_membership(seeds, previous, live)
        )
        await repository.set_runtime(campaign_id, round=1)
        await _round_started(ctx, 1)
    elif refresh:
        existing = await repository.list_targets(campaign_id)
        await repository.merge_targets(
            campaign_id,
            await targets.materialize(campaign_id, ctx.settings, live),
            round_number=record.round,
            removed=targets.removed_keys(existing, ctx.settings),
        )
    while True:
        record = await repository.fetch_campaign(campaign_id)
        if record is None:
            return "done"
        if record.rest_until_unix is not None:
            if not await _rest(ctx, record.round, record.rest_until_unix):
                return "done"
            continue
        outcome = await play_round(ctx, record.round)
        if outcome != "round_done":
            return "stalled" if outcome == "stalled" else "done"
        if _last_round(ctx, record.round) or not await _anything_left(campaign_id):
            await repository.finish_rounds(campaign_id)
            return "done"
        await _start_rest(ctx, record.round)


async def live_accounts(campaign_id: str) -> list[str]:
    """Roster accounts still in the run, held by it and not resting, in roster order."""
    return await _rotation.live_accounts(campaign_id)


def _last_round(ctx: RunContext, round_number: int) -> bool:
    return ctx.rounds is not None and round_number >= ctx.rounds


async def _anything_left(campaign_id: str) -> bool:
    """Is any chat waiting for the next round — through, or kept despite deletions?"""
    return any(
        t.state == "round_done"
        or (t.state == "skipped" and t.skip_reason == "deleted" and t.ignore_deleted)
        for t in await repository.list_targets(campaign_id)
    )


async def play_round(ctx: RunContext, round_number: int) -> RoundOutcome:
    live = await live_accounts(ctx.campaign_id)
    resting = await _rotation.resting_accounts(ctx.campaign_id)
    if not live and not resting:
        return await _stall(ctx)
    await _moves.adopt_orphans(ctx, live, keep=resting)
    await _moves.reclaim_waiting(ctx, live)
    tasks = _Tasks(ctx, round_number)
    for account_id in live:
        tasks.work(account_id)
    for account_id in resting:
        tasks.wake(account_id)
    try:
        while tasks.workers or tasks.wakers:
            done, _pending = await asyncio.wait(tasks.all(), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if await tasks.settle(task) == "volume":
                    return "volume"
            if tasks.workers:
                continue
            if not await _round_open(ctx, round_number):
                return "round_done"
            if not tasks.wakers:
                return await _stall(ctx)
        return "round_done"
    finally:
        await tasks.cancel()


class _Tasks:
    """One round's tasks: a worker per live account, a waker per resting one."""

    def __init__(self, ctx: RunContext, round_number: int) -> None:
        self.ctx = ctx
        self.round_number = round_number
        self.workers: dict[asyncio.Task[_worker.WorkerOutcome], str] = {}
        self.wakers: dict[asyncio.Task[bool], str] = {}

    def all(self) -> set[asyncio.Task[Any]]:
        return set(self.workers) | set(self.wakers)

    def work(self, account_id: str) -> None:
        task = asyncio.create_task(_worker.work(self.ctx, account_id, self.round_number))
        self.workers[task] = account_id

    def wake(self, account_id: str) -> None:
        self.wakers[asyncio.create_task(_rotation.sit_out(self.ctx, account_id))] = account_id

    async def settle(self, task: asyncio.Task[Any]) -> _worker.WorkerOutcome | None:
        """React to one finished task; the worker's outcome, ``None`` for a waker."""
        if task in self.wakers:
            account_id = self.wakers.pop(task)
            if task.result():
                await _moves.reclaim_waiting(self.ctx, await live_accounts(self.ctx.campaign_id))
                self.work(account_id)
            else:
                await _moves.hand_over(self.ctx, account_id, list(self.workers.values()))
            return None
        account_id = self.workers.pop(task)
        outcome: _worker.WorkerOutcome = task.result()
        if outcome == "halted":
            await _moves.hand_over(self.ctx, account_id, list(self.workers.values()))
        elif outcome == "resting":
            self.wake(account_id)
        return outcome

    async def cancel(self) -> None:
        pending = self.all()
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


async def _round_open(ctx: RunContext, round_number: int) -> bool:
    return any(
        t.state in _moves.ACTIVE_STATES and t.round == round_number
        for t in await repository.list_targets(ctx.campaign_id)
    )


async def _stall(ctx: RunContext) -> RoundOutcome:
    for target in await repository.list_targets(ctx.campaign_id):
        if target.state in _moves.ACTIVE_STATES and target.state not in {"joining", "captcha"}:
            await repository.update_target(
                ctx.campaign_id,
                target.chat_key,
                expect_states=_moves.ACTIVE_STATES,
                state="waiting_account",
            )
    return "stalled"


def rest_end(started_unix: int, bounds: MinutesRange) -> int:
    """When a rest begun at ``started_unix`` ends, drawn from the operator's range."""
    return started_unix + int(_seams.rng.uniform(bounds.min, bounds.max) * 60)


async def _start_rest(ctx: RunContext, round_number: int) -> None:
    started = _seams.now()
    until = rest_end(started, ctx.pace.rest_minutes)
    _state.note_rest_started(ctx.campaign_id, started)
    await repository.set_runtime(ctx.campaign_id, rest_until_unix=until)
    await log_event(
        "INFO",
        "chat_broadcast_rest_started",
        extra={"campaign_id": ctx.campaign_id, "round": round_number, "until": until},
    )


async def _rest(ctx: RunContext, round_number: int, until: int) -> bool:
    """Sit the rest out, then open the next round. ``False`` = the time budget ran out.

    Slept in slices, re-reading the end each time: the gear may move it mid-rest.
    """
    tick = settings.chat_broadcast.rest_poll_seconds
    while (remaining := until - _seams.now()) > 0:
        await _seams.sleep(min(remaining, tick))
        record = await repository.fetch_campaign(ctx.campaign_id)
        if record is not None and record.rest_until_unix is not None:
            until = record.rest_until_unix
    _seams.assert_live_run()
    if await _send.volume_reached(ctx):
        return False
    next_round = round_number + 1
    # Ends the rest and opens the round in the same write.
    await repository.start_round(ctx.campaign_id, next_round)
    await _round_started(ctx, next_round)
    return True


async def _round_started(ctx: RunContext, round_number: int) -> None:
    await log_event(
        "INFO",
        "chat_broadcast_round_started",
        extra={"campaign_id": ctx.campaign_id, "round": round_number},
    )
