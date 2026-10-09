"""One run: lay the chats out, play rounds with one task per account, rest between them.

A round ends when none of its chats is still open. Between rounds every chat rests;
the next round re-queues the ones that finished and keeps the ones that were skipped
for good — and since every account is already inside its chats, round 2 onward has no
joins and no pause after one, only the pauses between messages and chats.

An account that drops out (spam block, flood, errors in a row) hands its open chats to
the others at once; with nobody left the run ends ``stalled`` and the chats wait for
an account the operator adds before pressing Continue.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal

from core.config import settings
from core.logging import log_event
from core.repositories import chat_broadcast as repository
from services import _account_owner
from services.chat_broadcast import _moves, _seams, _send, _state, _worker, targets
from services.chat_broadcast._context import load_context

if TYPE_CHECKING:
    from schemas.chat_broadcast import MinutesRange
    from services.chat_broadcast._context import RunContext

RunOutcome = Literal["done", "stalled"]
RoundOutcome = Literal["round_done", "stalled", "volume"]
_OWNER = "chat_broadcast"


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
    """Roster accounts still in the run AND held by it, in roster order."""
    roster = await repository.list_roster(campaign_id)
    return [
        item.account_id
        for item in roster
        if item.state == "active"
        and _account_owner.owner_of(item.account_id) == _OWNER
        and _account_owner.holder_of(item.account_id) == campaign_id
    ]


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
    if not live:
        return await _stall(ctx)
    await _moves.adopt_orphans(ctx, live)
    tasks = {
        asyncio.create_task(_worker.work(ctx, account_id, round_number)): account_id
        for account_id in live
    }
    try:
        while tasks:
            done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                account_id = tasks.pop(task)
                outcome = task.result()
                if outcome == "volume":
                    return "volume"
                if outcome == "halted":
                    await _moves.hand_over(ctx, account_id, list(tasks.values()))
            if not tasks and await _round_open(ctx, round_number):
                return await _stall(ctx)
        return "round_done"
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


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
