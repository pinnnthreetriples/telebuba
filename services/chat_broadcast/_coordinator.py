"""One run: lay the chats out, then keep a task per account going until nothing is left.

Chats keep their own rounds (``_schedule``): a chat that played its chain rests on its
own and opens its next round when that rest is over, so nothing waits for anything else.
The supervisor here only keeps the tasks going — a worker per live account, a waker per
resting one, and two optional helpers (``_recovery``) that take accounts other features
release and try halted ones again — and ends the run when nothing is left to do.

An account Telegram or its own limit stops RESTS (``_rotation``): a waker task sits the
rest out. An account that dies (frozen, logged out, errors in a row) hands its open chats
to the others at once. A task that crashes unexpectedly takes only itself down: it is
counted as an error of its account, which starts again after a pause or, after a series,
leaves the run like any halted account. Only with no task at all and work left does the
run end ``stalled``, with a code that says why.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

from core.config import settings
from core.logging import log_event
from core.repositories import chat_broadcast as repository
from services.chat_broadcast import (
    _moves,
    _recovery,
    _rotation,
    _schedule,
    _seams,
    _send,
    _state,
    _worker,
    targets,
)
from services.chat_broadcast._context import load_context

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from services.chat_broadcast._context import RunContext

rest_end = _moves.rest_end
_Kind = Literal["worker", "waker", "claimer", "retrier"]


class RunOutcome(NamedTuple):
    """How a run ended: ``done``, or ``stalled`` with the code that says why."""

    status: Literal["done", "stalled"]
    code: str | None = None


async def run_campaign(campaign_id: str, run_id: str, *, refresh: bool) -> RunOutcome:
    ctx = await load_context(campaign_id, run_id)
    record = await repository.fetch_campaign(campaign_id)
    if ctx is None or record is None:
        return RunOutcome("done")
    live = await live_accounts(campaign_id)
    if record.round == 0:
        previous = await repository.list_targets(campaign_id)
        seeds = await targets.materialize(campaign_id, ctx.settings, live)
        await repository.replace_targets(
            campaign_id, targets.carry_membership(seeds, previous, live)
        )
        await repository.set_runtime(campaign_id, round=1)
    elif refresh:
        existing = await repository.list_targets(campaign_id)
        await repository.merge_targets(
            campaign_id,
            await targets.materialize(campaign_id, ctx.settings, live),
            # A chat added meanwhile has its own rounds, all of them: it starts at the first.
            round_number=1,
            removed=targets.removed_keys(existing, ctx.settings),
        )
    # A run that began with one rest for the whole round hands it to its chats.
    await repository.adopt_barrier_rest(campaign_id)
    return await play(ctx)


async def live_accounts(campaign_id: str) -> list[str]:
    """Roster accounts still in the run, held by it and not resting, in roster order."""
    return await _rotation.live_accounts(campaign_id)


async def play(ctx: RunContext) -> RunOutcome:
    """Supervise the run: make sure the tasks that should exist do, then wait for one to end.

    A fault of the supervisor's own (a database that hiccuped under ``ensure``, a failed
    hand-over) is logged and the loop goes on after a pause: the tasks and the rows are
    the truth, and the next pass repairs what the failed one left. Only the operator's
    decisions (Stop, "skip errors" off) and cancellation end the run early.
    """
    tasks = _Tasks(ctx)
    try:
        while True:
            try:
                outcome = await _supervise(ctx, tasks)
            except (
                asyncio.CancelledError,
                _seams.ChatBroadcastRunRevokedError,
                _moves.ChatBroadcastStoppedOnErrorError,
            ):
                raise
            except Exception as exc:  # noqa: BLE001 - a transient fault must not fail the run
                await log_event(
                    "ERROR",
                    "chat_broadcast_worker_crashed",
                    extra={"campaign_id": ctx.campaign_id, "error_type": type(exc).__name__},
                )
                await _seams.sleep(settings.chat_broadcast.idle_poll_seconds)
                continue
            if outcome is not None:
                return outcome
    finally:
        await tasks.cancel()


async def _supervise(ctx: RunContext, tasks: _Tasks) -> RunOutcome | None:
    """One pass of the supervisor; the outcome when the run is over, else ``None``."""
    _seams.assert_live_run()
    if await _send.volume_reached(ctx):
        return RunOutcome("done")
    chats = await repository.list_targets(ctx.campaign_id)
    if _schedule.nothing_left(chats, ctx.rounds):
        return RunOutcome("done") if await _finish(ctx) else None
    await tasks.ensure()
    if not tasks.all():
        return await _stall(ctx)
    if not tasks.workers and not tasks.wakers:
        # Only the helpers are left: their accounts may come back, the chats wait.
        if _gave_up_waiting(ctx, tasks):
            return await _stall(ctx, _recovery.STALL_BUSY)
        await _recovery.mark_waiting_account(ctx)
    else:
        _state.clear_stuck(ctx.campaign_id)
    done, _pending = await asyncio.wait(tasks.all(), return_when=asyncio.FIRST_COMPLETED)
    for task in done:
        if await tasks.settle(task) == "volume":
            return RunOutcome("done")
    return None


async def _finish(ctx: RunContext) -> bool:
    """Close the run when nothing is left; ``False`` = something turned up."""
    finished = await repository.try_finish(
        ctx.campaign_id,
        work_left=lambda chats: _schedule.work_left(chats, ctx.rounds),
        now_unix=_seams.now(),
    )
    if finished:
        return True
    record = await repository.fetch_campaign(ctx.campaign_id)
    # A campaign no longer running is not ours to wait on; settle writes what it is.
    return record is None or record.status != "running"


def _gave_up_waiting(ctx: RunContext, tasks: _Tasks) -> bool:
    """Has the run waited too long for accounts held elsewhere, with nothing else to wait for?

    Only a claimer alone counts: a retrier has its own bound, and a worker or a waker means
    work is going on. Taking an account starts the wait over.
    """
    if set(tasks.helpers) != {"claimer"}:
        _state.clear_stuck(ctx.campaign_id)
        return False
    now = _seams.now()
    since = _state.stuck_since(ctx.campaign_id, now)
    return now - since >= settings.chat_broadcast.claim_give_up_seconds


async def _stall(ctx: RunContext, cause: str | None = None) -> RunOutcome:
    await _recovery.mark_waiting_account(ctx)
    return RunOutcome("stalled", cause or await _recovery.stall_cause(ctx.campaign_id))


class _Tasks:
    """The run's tasks: a worker per live account, a waker per resting one, two helpers."""

    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx
        self.workers: dict[str, asyncio.Task[_worker.WorkerOutcome]] = {}
        self.wakers: dict[str, asyncio.Task[bool]] = {}
        self.helpers: dict[_Kind, asyncio.Task[bool]] = {}

    def all(self) -> set[asyncio.Task[Any]]:
        return {*self.workers.values(), *self.wakers.values(), *self.helpers.values()}

    async def ensure(self) -> None:
        """Start what should be running and is not. One task per account, never two."""
        live, resting = await _rotation.accounts_by_rest(self.ctx.campaign_id)
        for account_id in live:
            if account_id not in self.workers and account_id not in self.wakers:
                self.workers[account_id] = asyncio.create_task(_worker.work(self.ctx, account_id))
        for account_id in resting:
            if account_id not in self.workers and account_id not in self.wakers:
                self.wakers[account_id] = asyncio.create_task(
                    _rotation.sit_out(self.ctx, account_id)
                )
        campaign_id = self.ctx.campaign_id
        if "claimer" not in self.helpers and await _recovery.left_out(campaign_id):
            self.helpers["claimer"] = asyncio.create_task(_recovery.watch_left_out(self.ctx))
        if "retrier" not in self.helpers and await _recovery.retryable(campaign_id):
            self.helpers["retrier"] = asyncio.create_task(_recovery.watch_halted(self.ctx))

    def _forget(self, task: asyncio.Task[Any]) -> tuple[_Kind, str]:
        """Take a finished task out of the books; what it was and whose it was."""
        for account_id, worker in self.workers.items():
            if worker is task:
                del self.workers[account_id]
                return "worker", account_id
        for account_id, waker in self.wakers.items():
            if waker is task:
                del self.wakers[account_id]
                return "waker", account_id
        for kind, helper in self.helpers.items():
            if helper is task:
                del self.helpers[kind]
                return kind, ""
        msg = "a task nobody started"  # pragma: no cover - asyncio.wait only returns ours
        raise LookupError(msg)  # pragma: no cover

    async def settle(self, task: asyncio.Task[Any]) -> _worker.WorkerOutcome | None:
        """React to one finished task; the worker's outcome, ``None`` for any other."""
        kind, name = self._forget(task)
        try:
            result = task.result()
        except (
            asyncio.CancelledError,
            _seams.ChatBroadcastRunRevokedError,
            _moves.ChatBroadcastStoppedOnErrorError,
        ):
            # Stop and "skip errors off" are the operator's decisions, not faults to absorb.
            raise
        except Exception as exc:  # noqa: BLE001 - one task's fault must not end the run
            await self._crashed(kind, name, exc)
            return None
        ctx = self.ctx
        if kind == "worker":
            if result == "halted":
                await _moves.hand_over(ctx, name, list(self.workers))
            # "resting" gets its waker from the next ensure.
            return "volume" if result == "volume" else None
        if kind == "waker":
            if result:
                await _moves.reclaim_waiting(ctx, await live_accounts(ctx.campaign_id))
                await _schedule.tick_safely(ctx, force=True)
            else:
                await _moves.hand_over(ctx, name, list(self.workers))
            return None
        if result:
            # An account came back: it has no chats yet, and the balance gives it some.
            _state.clear_stuck(ctx.campaign_id)
            await _schedule.tick_safely(ctx, force=True)
        return None

    async def _crashed(self, kind: _Kind, name: str, exc: Exception) -> None:
        """One task died of something unexpected: log it, count it against its account.

        The account starts again after a pause (the same pause stops a fault that repeats
        from spinning); a series of crashes takes it out of the run like any halted one.
        """
        ctx = self.ctx
        await log_event(
            "ERROR",
            "chat_broadcast_worker_crashed",
            account_id=name or None,
            extra={"campaign_id": ctx.campaign_id, "error_type": type(exc).__name__},
        )
        if not name:
            # A helper has no account to blame: it is simply started again, later.
            self._restart_helper(kind)
            return
        # A join or captcha it died in the middle of is in nobody's hands now.
        await repository.settle_interrupted(ctx.campaign_id, account_id=name)
        if await _moves.account_error(ctx, name, failed=True):
            await _moves.halt_account(ctx, name, "worker_crash")
            await _moves.hand_over(ctx, name, list(self.workers))
        elif kind == "worker":
            self.workers[name] = asyncio.create_task(_after_pause(lambda: _worker.work(ctx, name)))
        else:
            self.wakers[name] = asyncio.create_task(
                _after_pause(lambda: _rotation.sit_out(ctx, name))
            )

    def _restart_helper(self, kind: _Kind) -> None:
        ctx = self.ctx
        if kind == "claimer":
            self.helpers[kind] = asyncio.create_task(
                _after_pause(lambda: _recovery.watch_left_out(ctx))
            )
        else:
            self.helpers[kind] = asyncio.create_task(
                _after_pause(lambda: _recovery.watch_halted(ctx))
            )

    async def cancel(self) -> None:
        pending = self.all()
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


async def _after_pause[T](start: Callable[[], Awaitable[T]]) -> T:
    """Start a task again, but not at once: a fault that repeats must not spin the run."""
    await _seams.sleep(settings.chat_broadcast.worker_restart_seconds)
    return await start()
