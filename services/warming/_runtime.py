"""Warming runtime — per-account loop tasks, start/stop, and the loop step.

Warming is a continuous randomised loop per account (cycle -> 12-30h sleep ->
repeat), so each running account owns an :class:`asyncio.Task` in ``_RUNTIME``.
``run_loop_iteration`` is the testable step; ``_warming_loop`` is the wrapper.

Telegram / Gemini / spam-probe / randomness are reached via
:mod:`services.warming._seams`; ``_in_quiet_hours`` is re-exported here so tests
patch it on this module.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import suppress
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    fetch_account,
    fetch_warming_state,
    get_listener_account_id,
    get_listener_running,
    get_spam_status,
    list_warming_channels,
    list_warming_states,
    load_warming_settings,
    unmark_promoted_to_nc,
)
from core.logging import log_event
from services.dialogues import assign_pairs
from services.trust import account_trust_score
from services.warming import _seams
from services.warming._purge import purge_stale_history
from services.warming._runner import _warming_loop
from services.warming._start_state import carry_or_restamp
from services.warming._state import _current_card, _set_state
from services.warming.pacing import (
    _now_iso,
    _proxy_snapshot,
    evaluate_readiness,
)

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.warming import (
        StartWarmingRequest,
        WarmingAccountState,
        WarmingReadiness,
        WarmingStateRecord,
    )

# Stdlib sink for full third-party text — see ``core.proxy_check._failed_result``.
logger = logging.getLogger(__name__)

# account_id -> running warming loop. Genuine runtime state (rare exception to
# the "no classes for stateless logic" rule): the loops must outlive a single
# UI handler call so the board can start/stop them.
_RUNTIME: dict[str, asyncio.Task[None]] = {}

# A revoked generation gets a distinct persisted marker. It fences every stale
# run_id CAS immediately and lets the task's done callback settle only that same
# timed-out stop to idle without racing a later Start. The prefix is what makes such
# a marker recognisable after a restart, when the task it named is gone — see
# ``_maintenance._settle_interrupted_stop``.
_STOPPING_MARKER_PREFIX = "stopping-"
_STOPPING_MARKERS: dict[asyncio.Task[None], tuple[str, str]] = {}
_STOP_FINALIZERS: set[asyncio.Task[None]] = set()

# Per-account async lock: prevents concurrent start/stop interleaving from
# leaving the DB and ``_RUNTIME`` in mismatched states. Locks are created lazily
# and never freed — the dictionary is bounded by the number of accounts.
_ACCOUNT_LOCKS: dict[str, asyncio.Lock] = {}

# Single background retention sweep, started with the runtime and cancelled on
# shutdown. A startup-only purge lets the append-only tables grow unbounded
# during long uptimes; this reruns it every ``warming.purge_interval_hours``.
_PURGE_TASK: asyncio.Task[None] | None = None


class UnknownAccountError(ValueError):
    """Raised when start/stop is called for an account that does not exist."""


class WarmingNotReadyError(ValueError):
    """Raised when ``start_warming`` refuses a not-ready account.

    Carries the structured ``reasons`` so the UI can show them to the user.
    """

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("; ".join(reasons) or "account not ready")


class AccountIsListenerError(ValueError):
    """Raised when ``start_warming`` refuses the running neurocomment listener.

    The reciprocal of neurocomment's ``ListenerBusyWarmingError``: the two runtimes
    are mutually exclusive per account, so an account cannot be warmed while it is
    the active listener.
    """


class WarmingTaskNotQuiescentError(RuntimeError):
    """The previous warming coroutine is still alive after bounded cancellation."""


def _account_lock(account_id: str) -> asyncio.Lock:
    lock = _ACCOUNT_LOCKS.get(account_id)
    if lock is None:
        lock = asyncio.Lock()
        _ACCOUNT_LOCKS[account_id] = lock
    return lock


def _discard_runtime_task(account_id: str, task: asyncio.Task[None]) -> None:
    """Release ownership only when ``task`` is still the registered task."""
    if _RUNTIME.get(account_id) is task:
        _RUNTIME.pop(account_id, None)


async def _settle_late_stop(account_id: str, stopping_marker: str) -> None:
    """Turn one timed-out stop idle iff its marker still owns the persisted row."""
    async with _account_lock(account_id):
        current = await fetch_warming_state(account_id)
        if current is None or current.run_id != stopping_marker:
            return
        await _set_state(
            account_id,
            "idle",
            last_event="stopped_after_timeout",
            last_error=None,
            stopped_at=_now_iso(),
            run_id=None,
            expected_run_id=stopping_marker,
        )


def _runtime_task_done(account_id: str, run_id: str, task: asyncio.Task[None]) -> None:
    """Done callback: terminal tasks leave both ownership registries atomically."""
    _discard_runtime_task(account_id, task)
    _seams.revoke_lease(account_id, run_id)


def _late_stopped_task_done(task: asyncio.Task[None]) -> None:
    """Schedule the CAS settle registered only after a bounded wait timed out."""
    stopped = _STOPPING_MARKERS.pop(task, None)
    if stopped is not None:
        stopped_account_id, marker = stopped
        finalizer = asyncio.create_task(_settle_late_stop(stopped_account_id, marker))
        _STOP_FINALIZERS.add(finalizer)
        finalizer.add_done_callback(_STOP_FINALIZERS.discard)


def _spawn_runtime_task(account_id: str, run_id: str) -> asyncio.Task[None]:
    """Create and register the sole task/lease pair for one account generation."""
    _seams.activate_lease(account_id, run_id)
    task = asyncio.create_task(_warming_loop(account_id, run_id=run_id))
    _RUNTIME[account_id] = task
    task.add_done_callback(
        lambda completed, aid=account_id, rid=run_id: _runtime_task_done(aid, rid, completed)
    )
    return task


async def _await_runtime_task(account_id: str, task: asyncio.Task[None]) -> bool:
    """Wait at most the configured budget; retain every non-terminal task."""
    if task.done():
        _discard_runtime_task(account_id, task)
        return True
    done, _pending = await asyncio.wait(
        {task},
        timeout=settings.warming.stop_cancel_timeout_seconds,
    )
    if not done:
        return False
    _discard_runtime_task(account_id, task)
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.exception("warming task failed while stopping %s", account_id)
        await log_event(
            "WARNING",
            "warming_stop_task_error",
            account_id=account_id,
            extra={"error_type": type(exc).__name__},
        )
    return True


async def _cancel_runtime_task(account_id: str, *, last_event: str) -> bool:
    """Revoke, cancel and bounded-wait without losing a cancellation-suppressing task.

    Also the operator's way out of a stop that ran out of time, which is what makes the
    resulting lockout defensible. Every lifecycle entry point that can refuse — Stop,
    Start's cancel-and-replace, and Promote (which stops first) — comes back through
    here, finds the retained task still non-terminal, and cancels it AGAIN. A coroutine
    that survived the first cancel because it was parked inside its own ``except
    CancelledError`` cleanup dies on the second, and the row settles to idle. Only a
    task that suppresses cancellation unconditionally stays owned, and there the
    refusal is the honest answer: something is still holding the session, and publishing
    a second generation on top of it is how one account ends up with two runtimes
    talking to Telegram. Handoff and Delete refuse without cancelling
    (``assert_runtime_quiescent``) because neither may replace the coroutine — the
    operator presses Stop first.
    """
    task = _RUNTIME.get(account_id)
    if task is None or task.done():
        if task is not None:
            _discard_runtime_task(account_id, task)
        _seams.revoke_lease(account_id)
        return True

    # Revoke before cancel: even if the task catches CancelledError, the seam's
    # pre/post-dispatch fence prevents another Telegram action from this point.
    _seams.revoke_lease(account_id)
    stopping_marker = f"{_STOPPING_MARKER_PREFIX}{uuid.uuid4().hex}"
    await _set_state(
        account_id,
        "error",
        last_event=last_event,
        last_error="warming task is stopping",
        heartbeat_at=_now_iso(),
        run_id=stopping_marker,
    )
    task.cancel()
    quiescent = await _await_runtime_task(account_id, task)
    if not quiescent:
        _STOPPING_MARKERS[task] = (account_id, stopping_marker)
        # add_done_callback also schedules the callback when the task completed
        # in the narrow window after asyncio.wait timed out.
        task.add_done_callback(_late_stopped_task_done)
    return quiescent


def assert_runtime_quiescent(account_id: str) -> None:
    """Refuse lifecycle hand-offs while an old warming coroutine still exists."""
    task = _RUNTIME.get(account_id)
    if task is not None and not task.done():
        raise WarmingTaskNotQuiescentError(account_id)


async def _evaluate_account_readiness(
    account_id: str,
    account: AccountRead,
    channel_count: int,
) -> WarmingReadiness:
    """Last-known-state readiness verdict, fetching the account's spam + trust."""
    return evaluate_readiness(
        account,
        channel_count,
        spam=await get_spam_status(account_id),
        trust_score=await account_trust_score(account_id),
    )


async def _enforce_start_readiness(account_id: str, account: AccountRead) -> None:
    """Refuse a not-ready account at start, raising ``WarmingNotReadyError``."""
    if not (await load_warming_settings()).enforce_readiness:
        return
    channel_count = len((await list_warming_channels()).channels)
    readiness = await _evaluate_account_readiness(account_id, account, channel_count)
    if readiness.ready:
        return
    await log_event(
        "WARNING",
        "warming_start_blocked",
        account_id=account_id,
        extra={"reasons": readiness.reasons},
    )
    raise WarmingNotReadyError(readiness.reasons)


async def _cancel_existing_task(account_id: str) -> None:
    """Cancel + await any in-flight loop task so a "start now" isn't blocked by it.

    F2: the task may still be inside the inter-cycle
    ``asyncio.sleep(_loop_sleep_seconds(...))`` from the *previous* ``next_run_at``.
    Clearing that schedule cannot wake a sleeping task, so cancel-and-replace is
    the only way to honour the operator's "start now".

    Refuses with ``WarmingTaskNotQuiescentError``, the same signal Promote / Handoff /
    Delete raise for the same condition — a transient lifecycle conflict (409), not a
    readiness verdict. ``WarmingNotReadyError`` sent it to the 400 branch and the SPA
    listed "previous warming task is still stopping" among the account's readiness
    reasons, next to a missing proxy: the operator was told the account is unfit to
    warm when the truth is "try again in a moment".
    """
    if await _cancel_runtime_task(account_id, last_event="restart_stopping"):
        return
    await log_event(
        "WARNING",
        "warming_restart_timeout",
        account_id=account_id,
    )
    raise WarmingTaskNotQuiescentError(account_id)


async def start_warming(data: StartWarmingRequest) -> WarmingAccountState:
    """Move an account into the warming column and kick off its loop task."""
    async with _account_lock(data.account_id):
        account = await fetch_account(data.account_id)
        if account is None:
            msg = f"Unknown account: {data.account_id}"
            raise UnknownAccountError(msg)
        # Reciprocal of the neurocomment listener guard: refuse to warm the account
        # that is the active listener, so the two runtimes never share a session.
        if await get_listener_running() and await get_listener_account_id() == data.account_id:
            raise AccountIsListenerError(data.account_id)
        await _enforce_start_readiness(data.account_id, account)
        # Revoke + cancel first, and publish a fresh generation only after the old
        # coroutine is terminal. A task that suppresses cancellation remains owned
        # and makes Start fail instead of overlapping Telegram activity. The
        # per-booking reservation token still lets normal cancellation hand unused
        # budget back before this new generation is minted.
        existing = await fetch_warming_state(data.account_id)
        await _cancel_existing_task(data.account_id)
        # P1.2: stamp a fresh generation marker so an in-flight cycle from
        # the previous run can detect and refuse to write through.
        run_id = uuid.uuid4().hex
        stint = carry_or_restamp(existing, data)
        # Bug 2: a previously-promoted account dragged back into warming would
        # otherwise live in both pools — clear the flag so neurocomment's
        # warmed-account overview drops it on the next poll.
        if existing is not None and existing.promoted_to_nc:
            await unmark_promoted_to_nc(data.account_id)
            await log_event(
                "INFO",
                "warming_unpromoted_on_restart",
                account_id=data.account_id,
            )
        try:
            await _set_state(
                data.account_id,
                "active",
                last_event="queued",
                next_run_at=None,
                started_at=stint.started_at,
                stopped_at=None,
                last_error=None,
                # П6: clear the previous run's furthest-step/channel so the just-
                # queued card shows "online", not a stale send_dm/react on an old
                # channel until the first cycle write lands.
                last_action=None,
                last_channel=None,
                flood_wait_seconds=None,
                flood_wait_until=None,
                proxy_snapshot=_proxy_snapshot(account),
                run_id=run_id,
                target_days=stint.target_days,
                activity_persona=stint.activity_persona,
            )
            # Build dialogue ownership before the loop can run its first cycle.
            # Cancellation in this awaited preparation is covered by the same
            # rollback as cancellation during the state commit.
            await _refresh_dialogue_pairs()
        except asyncio.CancelledError:
            # The write may already have committed when cancellation surfaced.
            # Revoke it explicitly so DB=active can never be left without a task.
            cleanup = asyncio.create_task(
                _set_state(
                    data.account_id,
                    "idle",
                    last_event="start_cancelled",
                    stopped_at=_now_iso(),
                    run_id=None,
                    expected_run_id=run_id,
                )
            )
            with suppress(asyncio.CancelledError):
                await asyncio.shield(cleanup)
            raise
        # No await after the prepared generation and task registration: a request
        # cancellation can now only land after ownership is complete.
        _spawn_runtime_task(data.account_id, run_id)
    await log_event("INFO", "warming_started", account_id=data.account_id)
    return await _current_card(data.account_id)


def account_lock(account_id: str) -> asyncio.Lock:
    """Expose the lifecycle lock for compound service-level operations."""
    return _account_lock(account_id)


async def _refresh_dialogue_pairs() -> None:
    try:
        await assign_pairs()
    except Exception as exc:  # reconcile must not fail because dialogues did.
        logger.exception("dialogue pair refresh failed")
        await log_event(
            "WARNING",
            "warming_dialogue_pair_refresh_failed",
            extra={"error_type": type(exc).__name__},
        )


async def _list_runtime_states() -> list[WarmingStateRecord]:
    return await list_warming_states()


async def _purge_runtime_history() -> None:
    await purge_stale_history()


from services.warming._graduation import (  # noqa: E402, F401 - re-export after globals are defined.
    _stop_warming_locked,
    handoff_to_neurocomment,
    promote_to_neurocomment,
    stop_warming,
    unmark_neurocomment,
)
from services.warming._maintenance import (  # noqa: E402, F401 - lifecycle re-exports.
    _purge_loop,
    _start_purge_task,
    _stop_purge_task,
    reconcile_warming_runtime,
    shutdown_warming_runtime,
)
