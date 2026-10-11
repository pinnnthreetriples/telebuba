"""One account's loop through the list, for as long as it may work today.

Accounts work in parallel, each taking the next batch nobody holds: one story's worth
of people, or one person in priming. A primed person waits for the story of the account
that opened their dialog; a contact (or a group member without a username) only for the
account that found them. The loop re-reads the list every pass, so a «Сейчас» from the
board or a target another account gave back is picked up without a restart.

It ends with what the account does next: ``done`` (nothing left for it), ``resting``
(its day is spent), ``flood_out`` (floods in a row — it rests until tomorrow),
``halted`` (spam block or a dead session — it leaves the run) or ``volume`` (the
operator's "stop after N").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from core.config import settings
from core.logging import log_event
from core.repositories import masstagging as repository
from core.repositories.spam_status import upsert_spam_status
from schemas.masstagging_records import JournalRecord
from schemas.spam_status import SpamStatusVerdict
from services.masstagging import _fleet, _moves, _seams, _state, _steps
from services.masstagging.pace import batch_size, day_of, gap_seconds, stories_cap

if TYPE_CHECKING:
    from schemas.masstagging_records import TargetRecord
    from services.masstagging._context import RunContext
    from services.masstagging._fleet import FleetAccount
    from services.masstagging.pace import AccountView

WorkerOutcome = Literal["done", "resting", "flood_out", "halted", "volume"]


async def volume_reached(ctx: RunContext, account_id: str) -> bool:
    """The operator's "stop after N tags / N minutes", per account since Start/Continue."""
    pace = ctx.pace
    if pace.stop_mode == "time":
        return pace.stop_minutes > 0 and _seams.now() >= ctx.session_unix + pace.stop_minutes * 60
    if pace.stop_tags <= 0:
        return False
    tagged = await repository.count_tagged_since(ctx.campaign_id, account_id, ctx.session_unix)
    return tagged >= pace.stop_tags


def takes(target: TargetRecord, account_id: str) -> bool:
    """May ``account_id`` take this target (its own primed people, its own contacts)?"""
    if target.assigned_account_id not in {None, account_id}:
        return False
    return target.state != "primed" or target.account_id == account_id


def _pick(due: list[TargetRecord], account_id: str, size: int) -> list[TargetRecord]:
    """Its own primed people first — they wait for its story — then the list in order."""
    primed = [t for t in due if t.state == "primed" and t.account_id == account_id]
    rest = [t for t in due if t not in primed]
    return (primed + rest)[:size]


@dataclass
class _Step:
    """One batch in flight: who plays it, and which of its people this account primed."""

    account_id: str
    view: AccountView
    keys: set[str]
    primed: set[str] = field(default_factory=set)


async def _view(ctx: RunContext, account: FleetAccount) -> AccountView:
    views = await _fleet.views({account.account_id: account}, ctx.pace, _seams.now())
    return views[account.account_id]


async def work(ctx: RunContext, account: FleetAccount) -> WorkerOutcome:
    while True:
        _seams.assert_live_run()
        if await volume_reached(ctx, account.account_id):
            return "volume"
        view = await _view(ctx, account)
        next_move = await _next(ctx, view)
        if isinstance(next_move, str):
            return next_move
        if isinstance(next_move, int):
            await _seams.sleep(next_move)
            continue
        outcome = await _step(ctx, view, next_move)
        if outcome is not None:
            return outcome


async def _next(ctx: RunContext, view: AccountView) -> WorkerOutcome | int | list[TargetRecord]:
    """The account's next batch, claimed; or how long to wait; or why it stops."""
    account_id = view.account_id
    poll = int(settings.masstagging.idle_poll_seconds)
    now = _seams.now()
    if view.day.floods_in_row >= ctx.pace.floods_to_rest:
        return "flood_out"
    if view.day.paused_until_unix is not None and view.day.paused_until_unix > now:
        return min(view.day.paused_until_unix - now, poll)
    size = batch_size(view, ctx.settings.mode, ctx.pace)
    if size <= 0:
        return "resting"
    targets = await repository.list_targets(ctx.campaign_id)
    mine = [t for t in targets if t.state in _moves.PICKABLE and takes(t, account_id)]
    if not mine:
        return "done"
    due = [t for t in mine if (t.next_action_unix or 0) <= now]
    if not due:
        upcoming = min(t.next_action_unix or now for t in mine)
        return min(max(1, upcoming - now), poll)
    batch = _pick(due, account_id, size)
    claimed = set(
        await repository.update_targets(
            ctx.campaign_id,
            [t.target_key for t in batch],
            at=now,
            expect_states=_moves.PICKABLE,
            state="working",
            account_id=account_id,
            next_action_unix=None,
        )
    )
    return [t for t in batch if t.target_key in claimed] or 1


async def _step(
    ctx: RunContext, view: AccountView, batch: list[TargetRecord]
) -> WorkerOutcome | None:
    """Play one claimed batch, then pause; ``None`` = the account goes on."""
    step = _Step(
        account_id=view.account_id,
        view=view,
        keys={t.target_key for t in batch},
        primed={t.target_key for t in batch if t.state == "primed"},
    )
    try:
        acted = await _play(ctx, step, batch)
    except _steps.StepSignal as signal:
        return await _on_signal(ctx, step, signal)
    if not acted:
        # Nobody in the batch reached Telegram: no pause is owed, take the next one.
        return None
    if view.day.floods_in_row:
        await repository.set_day(view.account_id, view.day.day, floods_in_row=0)
    gap = gap_seconds(view, ctx.settings.mode, ctx.pace, _seams.rng.random())
    _state.note_next_step(ctx.campaign_id, view.account_id, _seams.now() + gap)
    await _seams.sleep(gap)
    return None


async def _resolved(
    ctx: RunContext, account_id: str, batch: list[TargetRecord]
) -> list[tuple[TargetRecord, TargetRecord]]:
    """Who each person is, minus anyone any campaign already tagged (one registry read)."""
    found: list[tuple[TargetRecord, TargetRecord]] = []
    for target in batch:
        current = await _steps.resolve(ctx, account_id, target)
        if current is not None:
            found.append((target, current))
    if not ctx.pace.skip_tagged:
        return found
    ids = [c.resolved_user_id for _t, c in found if c.resolved_user_id is not None]
    tagged = await repository.tagged_user_ids(ids)
    for _target, current in [pair for pair in found if pair[1].resolved_user_id in tagged]:
        await _moves.skip(ctx, current, "already_tagged", account_id)
    return [pair for pair in found if pair[1].resolved_user_id not in tagged]


async def _play(ctx: RunContext, step: _Step, batch: list[TargetRecord]) -> bool:
    """One batch; ``True`` when a write went to Telegram (the pause after it is due)."""
    account_id, primed = step.account_id, step.primed
    ready: list[TargetRecord] = []
    acted = False
    for target, current in await _resolved(ctx, account_id, batch):
        if not ctx.stories:
            acted = True
            if await _steps.toggle(ctx, account_id, current):
                await _moves.tagged(ctx, [current], account_id)
            continue
        own_dialog = target.target_key in primed and target.account_id == account_id
        if ctx.settings.prime_first and not own_dialog:
            acted = True
            if not await _steps.toggle(ctx, account_id, current):
                continue
            await _moves.primed(ctx, current, account_id)
            primed.add(current.target_key)
        ready.append(current)
    if ready:
        await _steps.post_story(ctx, account_id, step.view, ready)
    return acted or bool(ready)


async def _held(ctx: RunContext, keys: set[str]) -> list[TargetRecord]:
    """The batch's targets the step did not finish."""
    targets = await repository.list_targets(ctx.campaign_id)
    return [t for t in targets if t.target_key in keys and t.state == "working"]


async def _on_signal(
    ctx: RunContext, step: _Step, signal: _steps.StepSignal
) -> WorkerOutcome | None:
    """Put the unfinished batch back and decide what the account does next."""
    now = _seams.now()
    account_id, view = step.account_id, step.view
    held = await _held(ctx, step.keys)
    own = [t for t in held if t.target_key in step.primed]
    others = [t for t in held if t.target_key not in step.primed]
    if signal.verdict == "flood":
        return await _flood(ctx, step, own, others, signal.seconds)
    if signal.verdict == "too_much":
        await _moves.put_back(ctx, own, "primed", account_id=account_id)
        await _moves.put_back(ctx, others, "queued", account_id=None)
        spent = stories_cap(view, ctx.pace) - view.day.stories
        if spent > 0:
            await repository.bump_day(account_id, view.day.day, stories=spent)
        await _rested(ctx, account_id, "stories_limit")
        return "resting"
    if signal.verdict == "reconnect":
        delay = int(settings.masstagging.reconnect_delay_seconds)
        await _moves.put_back(ctx, own, "primed", account_id=account_id)
        await _moves.put_back(ctx, others, "queued", account_id=None)
        await repository.set_day(account_id, view.day.day, paused_until_unix=now + delay)
        return None
    ctx.halted.add(account_id)
    await _halt(ctx, account_id, held, signal.verdict)
    return "halted"


async def _flood(
    ctx: RunContext,
    step: _Step,
    own: list[TargetRecord],
    others: list[TargetRecord],
    seconds: int | None,
) -> WorkerOutcome | None:
    now = _seams.now()
    account_id, view = step.account_id, step.view
    pause = max(ctx.pace.flood_pause_seconds, seconds or 0)
    floods = view.day.floods_in_row + 1
    await repository.set_day(
        account_id, day_of(now), floods_in_row=floods, paused_until_unix=now + pause
    )
    await _moves.put_back(ctx, own, "primed", account_id=account_id)
    await _moves.put_back(ctx, others, "flood", account_id=account_id, next_action_unix=now + pause)
    await repository.add_events(
        ctx.campaign_id,
        [
            JournalRecord(
                target_key=t.target_key,
                event="flood",
                at_unix=now,
                account_id=account_id,
                detail=str(pause),
            )
            for t in others
        ],
    )
    await log_event(
        "WARNING",
        "masstagging_flood_pause",
        account_id=account_id,
        extra={"campaign_id": ctx.campaign_id, "seconds": pause, "floods_in_row": floods},
    )
    if floods >= ctx.pace.floods_to_rest:
        await _rested(ctx, account_id, "floods")
        return "flood_out"
    return None


async def _rested(ctx: RunContext, account_id: str, reason: str) -> None:
    await log_event(
        "WARNING",
        "masstagging_account_rested",
        account_id=account_id,
        extra={"campaign_id": ctx.campaign_id, "reason": reason},
    )


async def _halt(ctx: RunContext, account_id: str, held: list[TargetRecord], verdict: str) -> None:
    """The account leaves the run; its batch and its primed people go back to the list."""
    targets = await repository.list_targets(ctx.campaign_id)
    waiting = [t for t in targets if t.state == "primed" and t.account_id == account_id]
    await repository.update_targets(
        ctx.campaign_id,
        [t.target_key for t in held + waiting],
        at=_seams.now(),
        expect_states=("working", "primed"),
        state="queued",
        reason=None,
        account_id=None,
    )
    extra: dict[str, object] = {"campaign_id": ctx.campaign_id}
    if verdict == "spamblock":
        await upsert_spam_status(
            SpamStatusVerdict(
                account_id=account_id,
                status="limited",
                detail="peer_flood",
                checked_at=datetime.fromtimestamp(_seams.now(), UTC).isoformat(),
            )
        )
        await log_event("ERROR", "masstagging_spamblock", account_id=account_id, extra=extra)
        return
    await log_event(
        "ERROR",
        "masstagging_account_halted",
        account_id=account_id,
        extra=extra | {"reason": verdict},
    )
