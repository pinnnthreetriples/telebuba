"""The scheduler: every chat has its own rounds, and the chats are kept evenly spread.

A chat that finished its chain rests on its own (``round_done`` with the end of the rest
in ``next_action_unix``) and opens its next round when that end comes; nothing waits for
the other chats of the round. One idempotent :func:`tick` per campaign keeps the rest in
order, under a lock and no more often than ``schedule_tick_seconds``:

1. a chat a barrier-round run left waiting without an end gets a rest from now on;
2. every chat whose rest is over opens its next round, whoever owns it, so a chat of a
   dead owner cannot hang;
3. chats of an account that is gone go to the live ones;
4. the chats of a resting account are placed by the rest it has left;
5. chats left waiting for an account go back to work;
6. the load is balanced between the live accounts (:func:`rebalance`);
7. the campaign's ``round`` is brought to the least advanced chat that is not finished.

Nothing here waits for a foreign lock or a Telegram answer: a tick that stood behind
another feature's account lock would stall every worker that calls it.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from core.repositories import chat_broadcast as repository
from services.chat_broadcast import _join, _moves, _rotation, _seams, _state
from services.chat_broadcast._moves import more_rounds, open_load, rest_end

if TYPE_CHECKING:
    from collections.abc import Sequence

    from schemas.chat_broadcast_records import TargetRecord
    from services.chat_broadcast._context import RunContext

__all__ = [
    "current_round",
    "is_resting",
    "nothing_left",
    "open_load",
    "promote",
    "rebalance",
    "tick",
    "tick_safely",
    "work_left",
]


def is_resting(target: TargetRecord) -> bool:
    """Out of the game for now, back by itself when its rest is over."""
    if target.state == "round_done":
        return True
    if target.state != "skipped":
        return False
    return target.skip_reason == "error" or (
        target.skip_reason == "deleted" and target.ignore_deleted
    )


def will_return(target: TargetRecord, rounds: int | None) -> bool:
    """Resting, and with a round to come back to: not a chat that is through for good."""
    return is_resting(target) and (rounds is None or target.round < rounds)


def nothing_left(targets: Sequence[TargetRecord], rounds: int | None) -> bool:
    """No chat is being played, none rests, none will come back for a round it has left."""
    return not work_left(targets, rounds)


def work_left(targets: Sequence[TargetRecord], rounds: int | None) -> bool:
    """Is any chat still in play, resting for its next round, or due a retry?"""
    return any(t.state in _moves.OPEN_STATES or will_return(t, rounds) for t in targets)


def current_round(targets: Sequence[TargetRecord], fallback: int) -> int:
    """The round of the least advanced chat not finished, else of the furthest one.

    What the board and the campaign row call "the round" now that chats do not share one.
    """
    chats = [t for t in targets if t.skip_reason != "removed"]
    unfinished = [t.round for t in chats if t.state in _moves.OPEN_STATES]
    if unfinished:
        return min(unfinished)
    return max((t.round for t in chats), default=fallback)


async def tick(ctx: RunContext, *, force: bool = False) -> None:
    """One pass over the campaign's chats; a pass that came too soon after the last is skipped."""
    async with _state.tick_lock(ctx.campaign_id):
        _seams.assert_live_run()
        now = _seams.now()
        last = _state.last_tick(ctx.campaign_id)
        too_soon = last is not None and now - last < settings.chat_broadcast.schedule_tick_seconds
        if too_soon and not force:
            return
        _state.note_tick(ctx.campaign_id, now)
        await _plan_barrier_rests(ctx, await repository.list_targets(ctx.campaign_id))
        await promote(ctx, await repository.list_targets(ctx.campaign_id))
        live, resting = await _rotation.accounts_by_rest(ctx.campaign_id)
        await _moves.adopt_orphans(ctx, live, keep=resting)
        await _rotation.place_resting(ctx)
        await _moves.reclaim_waiting(ctx, live)
        await rebalance(ctx, live)
        await _publish_round(ctx)


async def tick_safely(
    ctx: RunContext, *, force: bool = False, account_id: str | None = None
) -> None:
    """A tick whose failure is the scheduler's, not the caller's.

    A worker that called a failing tick would be counted as crashed — and every worker
    calls it, so a fault in the scheduler would take healthy accounts out of the run. It is
    logged instead, and tried again on the next call (the throttle has already moved on).
    """
    try:
        await tick(ctx, force=force)
    except (asyncio.CancelledError, _seams.ChatBroadcastRunRevokedError):
        raise
    except Exception as exc:  # noqa: BLE001 - the scheduler's fault must not end a worker
        await log_event(
            "ERROR",
            "chat_broadcast_worker_crashed",
            account_id=account_id,
            extra={"campaign_id": ctx.campaign_id, "error_type": type(exc).__name__},
        )


async def _plan_barrier_rests(ctx: RunContext, targets: Sequence[TargetRecord]) -> None:
    """A run that began with a shared rest left chats waiting for no end in particular.

    They rest from now: sending them into their next round at once would be a burst the
    operator's rest was there to prevent.
    """
    now = _seams.now()
    for target in targets:
        if target.next_action_unix is not None or not is_resting(target):
            continue
        if not more_rounds(ctx, target):
            continue
        until = rest_end(now, ctx.pace.rest_minutes)
        if await repository.move_target_rest(
            ctx.campaign_id,
            target.chat_key,
            round_number=target.round,
            until_unix=until,
            unscheduled=True,
        ):
            _state.note_rest_started(ctx.campaign_id, target.chat_key, now)


async def promote(
    ctx: RunContext, targets: Sequence[TargetRecord], *, owner: str | None = None
) -> int:
    """Open the next round of every chat whose rest is over; the count promoted.

    ``owner`` limits it to that account's chats (a worker advancing its own, between
    two ticks). The write is a compare-and-set on the round the chat leaves, so a chat
    two callers promote at once goes one round on, not two. A chat that rested through
    the last round is done.
    """
    now = _seams.now()
    moved = 0
    for target in targets:
        if (owner is not None and target.assigned_account_id != owner) or not is_resting(target):
            continue
        if not more_rounds(ctx, target):
            if target.state == "round_done" and await repository.update_target(
                ctx.campaign_id,
                target.chat_key,
                expect_states=("round_done",),
                state="done",
                next_action_unix=None,
            ):
                moved += 1
            continue
        if target.next_action_unix is None or target.next_action_unix > now:
            continue
        if await repository.promote_target(
            ctx.campaign_id,
            target.chat_key,
            round_number=target.round,
            now_unix=now,
            max_round=ctx.rounds,
        ):
            moved += 1
            _state.forget_rest(ctx.campaign_id, target.chat_key)
            await log_event(
                "INFO",
                "chat_broadcast_round_started",
                extra={
                    "campaign_id": ctx.campaign_id,
                    "chat": _moves.chat_ref(target),
                    "round": target.round + 1,
                },
            )
    return moved


def _cost(target: TargetRecord, receiver: str) -> int:
    """What it costs ``receiver`` to take this chat over: 0 it is inside, 1 nobody is."""
    if target.member_account_id == receiver:
        return 0
    return 1 if target.member_account_id is None else 2


def _pick(
    targets: Sequence[TargetRecord],
    donor: str,
    receiver: str,
    excluded: set[str],
    *,
    receiver_at_join_cap: bool,
) -> TargetRecord | None:
    """The cheapest chat of ``donor`` for ``receiver``.

    One the receiver is inside costs least, then one nobody has entered, then any other;
    a resting chat goes after the ones about to be played, then the run's own order. A chat
    the receiver is not inside and has no way into (a group known by its id alone) is not
    for it: it would be lost, not moved. Nor — for a receiver that has spent its join
    budget — one it would have to join. A chat the operator handed over by hand is not moved
    either. ``excluded`` are chats off the table for it (lost to a change under the balance,
    or banned or muted the receiver).
    """
    movable = [
        t
        for t in targets
        if t.assigned_account_id == donor
        and t.state in _moves.REBALANCE_STATES
        and not t.pinned
        and t.chat_key not in excluded
        and (t.member_account_id == receiver or (_join.can_join(t) and not receiver_at_join_cap))
    ]
    return min(
        movable,
        key=lambda t: (_cost(t, receiver), t.state == "round_done", t.position),
        default=None,
    )


async def _balance_step(
    ctx: RunContext,
    targets: Sequence[TargetRecord],
    accounts: tuple[list[str], list[str], dict[str, int], set[str]],
    blocked: set[str],
) -> tuple[TargetRecord | None, str] | None:
    """One move of the walk.

    ``(chat, receiver)`` when a chat moved, ``(None, receiver)`` for a move lost to a change
    under it (try again), ``None`` when no pair of accounts has one left.

    The idlest receiver is tried first, the busiest donor to it first; a donor with nothing
    movable (all in flight, or nothing the receiver can take) gives way to the next.
    """
    live, receivers, order, capped = accounts
    load = open_load(targets)
    donors = sorted(live, key=lambda a: (-load[a], order[a]))
    for receiver in sorted(receivers, key=lambda a: (load[a], order[a])):
        for donor in donors:
            if load[donor] - load[receiver] < settings.chat_broadcast.rebalance_min_gap:
                break
            refused = {t.chat_key for t in targets if receiver in t.refused_by}
            chosen = _pick(
                targets, donor, receiver, blocked | refused, receiver_at_join_cap=receiver in capped
            )
            if chosen is None:
                continue
            if await _moves.assign(
                ctx, chosen, receiver, expect_states=_moves.REBALANCE_STATES, record_origin=False
            ):
                return chosen, receiver
            blocked.add(chosen.chat_key)
            return None, receiver
    return None


async def rebalance(ctx: RunContext, live: list[str]) -> int:
    """Even the open chats out between the live accounts; the count moved.

    The most loaded live account gives to the least loaded one that may write now (live,
    active, not at its limit) while they differ by ``rebalance_min_gap`` or more; a donor
    with nothing it can give (all in flight, or nothing the receiver may take) gives way to
    the next donor, and a receiver nobody can give to, to the next receiver. Every
    move narrows the gap by two, so the walk ends, a move back is never due, and a second
    pass finds nothing to do. Nothing in flight is moved, nor a chat the operator pinned by
    hand, and the chat's origin is kept: see :func:`services.chat_broadcast._moves.assign`.
    """
    if len(live) <= 1:
        return 0
    receivers = [a for a in live if await _rotation.limit_ends(ctx, a) is None]
    if not receivers:
        return 0
    order = {account: index for index, account in enumerate(live)}
    capped = {a for a in receivers if await _join.at_join_cap(a)}
    targets = await repository.list_targets(ctx.campaign_id)
    blocked: set[str] = set()
    moved = 0
    while True:
        step = await _balance_step(ctx, targets, (live, receivers, order, capped), blocked)
        if step is None:
            break
        chosen, receiver = step
        if chosen is None:
            continue
        moved += 1
        targets = [
            t.model_copy(update={"assigned_account_id": receiver}) if t is chosen else t
            for t in targets
        ]
    if moved:
        await log_event(
            "INFO",
            "chat_broadcast_chats_handed",
            extra={"campaign_id": ctx.campaign_id, "count": moved},
        )
    return moved


async def _publish_round(ctx: RunContext) -> None:
    """Keep the campaign's ``round`` at the least advanced chat; written on a change only."""
    record = await repository.fetch_campaign(ctx.campaign_id)
    if record is None or record.round == 0:
        return
    targets = await repository.list_targets(ctx.campaign_id)
    round_now = current_round(targets, record.round)
    if round_now != record.round:
        await repository.set_runtime(ctx.campaign_id, round=round_now)
