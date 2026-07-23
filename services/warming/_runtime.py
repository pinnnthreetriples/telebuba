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
import uuid
from contextlib import suppress
from typing import TYPE_CHECKING, NamedTuple

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
from schemas.warming import (
    is_warming,
)
from services.dialogues import assign_pairs
from services.trust import account_trust_score
from services.warming._purge import purge_stale_history
from services.warming._runner import _warming_loop
from services.warming._state import _current_card, _set_state
from services.warming.pacing import (
    _now_iso,
    _proxy_snapshot,
    evaluate_readiness,
)

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.warming import (
        ActivityPersona,
        StartWarmingRequest,
        WarmingAccountState,
        WarmingReadiness,
        WarmingStateRecord,
    )

# account_id -> running warming loop. Genuine runtime state (rare exception to
# the "no classes for stateless logic" rule): the loops must outlive a single
# UI handler call so the board can start/stop them.
_RUNTIME: dict[str, asyncio.Task[None]] = {}

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


def _account_lock(account_id: str) -> asyncio.Lock:
    lock = _ACCOUNT_LOCKS.get(account_id)
    if lock is None:
        lock = asyncio.Lock()
        _ACCOUNT_LOCKS[account_id] = lock
    return lock


class _CarriedStint(NamedTuple):
    """The started_at / target_days / activity_persona a start should apply.

    П7: a restart-while-warming carries the original stint anchor, operator
    target, and persona; a genuine (re)start from idle/stopped restamps them.
    """

    started_at: str
    target_days: int
    activity_persona: ActivityPersona


def _carry_or_restamp(
    existing: WarmingStateRecord | None, data: StartWarmingRequest
) -> _CarriedStint:
    """Decide the stint fields once: carry the in-flight ones, restamp the rest."""
    started_at = (
        existing.started_at
        if existing and existing.started_at and is_warming(existing.state)
        else _now_iso()
    )
    target_days = (
        existing.target_days
        if existing is not None and existing.target_days and is_warming(existing.state)
        else (data.target_days or settings.neurocomment.warmed_min_days)
    )
    activity_persona = (
        existing.activity_persona
        if existing is not None and is_warming(existing.state)
        else data.activity_persona
    )
    return _CarriedStint(started_at, target_days, activity_persona)


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
    We just cleared that schedule, so cancel-and-replace is the only way to honour
    the operator's "start now".
    """
    existing = _RUNTIME.pop(account_id, None)
    if existing is not None and not existing.done():
        existing.cancel()
        with suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(
                asyncio.shield(existing),
                timeout=settings.warming.stop_cancel_timeout_seconds,
            )


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
        # P1.2: stamp a fresh generation marker so an in-flight cycle from
        # the previous run can detect and refuse to write through.
        run_id = uuid.uuid4().hex
        existing = await fetch_warming_state(data.account_id)
        stint = _carry_or_restamp(existing, data)
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
        await _cancel_existing_task(data.account_id)
        await _refresh_dialogue_pairs()
        _RUNTIME[data.account_id] = asyncio.create_task(
            _warming_loop(data.account_id, run_id=run_id),
        )
    await log_event("INFO", "warming_started", account_id=data.account_id)
    return await _current_card(data.account_id)


def account_lock(account_id: str) -> asyncio.Lock:
    """Public accessor for the per-account lifecycle lock (P2.2).

    Use this from a service-level operation that needs to hold the same lock
    ``start_warming`` / ``stop_warming`` / ``reconcile_warming_runtime`` use,
    e.g. to serialize stop + delete in ``remove_account``. The bare locked
    primitive (rather than a context manager wrapper) keeps the call site
    explicit about lock scope.
    """
    return _account_lock(account_id)


async def reconcile_warming_runtime() -> None:
    """Re-attach loop tasks for accounts whose DB state says they were running.

    ``_RUNTIME`` lives in process memory: after a restart the DB still shows
    ``active``/``sleeping``/``flood_wait`` but no task exists. We restart the
    loop for each such account so the board does not lie.

    Also refreshes the inter-account dialogue graph — ``assign_pairs`` is the
    only path that materialises pairs, so without this call the feature stays
    silently dormant. The call is idempotent (it rebuilds only when stale or
    membership-changed), so running it on every reconcile is cheap.
    """
    records = await list_warming_states()
    controls = await load_warming_settings()
    channel_count = len((await list_warming_channels()).channels)
    restarted = 0
    for record in records:
        # ``error`` is part of ``_ACTIVE_STATES`` so the UI keeps the card in
        # the warming column, but reconcile must not auto-resurrect a broken
        # account — the operator has to acknowledge and restart it.
        if not is_warming(record.state) or record.state == "error":
            continue
        # F3: take the same per-account lock as start/stop. Reconcile reads
        # state then spawns a task; without the lock, a parallel stop can
        # interleave and we end up with DB=idle + a live task.
        async with _account_lock(record.account_id):
            # Re-read inside the lock — stop_warming may have flipped this row
            # between the listing and acquiring the lock.
            fresh = await fetch_warming_state(record.account_id)
            if fresh is None or not is_warming(fresh.state) or fresh.state == "error":
                continue
            existing = _RUNTIME.get(record.account_id)
            if existing is not None and not existing.done():
                continue
            account = await fetch_account(record.account_id)
            if account is None:
                # Orphan state row — mark it stopped so the board is honest.
                await _set_state(
                    record.account_id,
                    "idle",
                    last_event="reconcile_orphan",
                    stopped_at=_now_iso(),
                )
                continue
            # Only gate the operator-startable cycling states. quarantine and
            # flood_wait are engine-managed recovery/cooldown states with their
            # own gates (a quarantined account is *expected* to read spam=limited
            # while it re-probes); applying the start_warming readiness gate to
            # them would abort an in-progress recovery and park it in error.
            if controls.enforce_readiness and fresh.state in ("active", "sleeping"):
                readiness = await _evaluate_account_readiness(
                    record.account_id,
                    account,
                    channel_count,
                )
                if not readiness.ready:
                    # Same gate as start_warming: a proxy that died / a fresh
                    # spam-limit / trust-critical drift mid-warming must not be
                    # silently resurrected on restart (start_warming would
                    # refuse it). Park it so the operator has to acknowledge.
                    await _set_state(
                        record.account_id,
                        "error",
                        last_event="reconcile_not_ready",
                        last_error="; ".join(readiness.reasons),
                        heartbeat_at=_now_iso(),
                    )
                    await log_event(
                        "WARNING",
                        "warming_reconcile_not_ready",
                        account_id=record.account_id,
                        extra={"reasons": readiness.reasons},
                    )
                    continue
            # P1.2: mint a fresh generation marker so this restarted loop owns
            # the row going forward; any pre-restart cycle that somehow lives
            # on (it shouldn't, post-restart, but be defensive) will see the
            # mismatch and bail.
            run_id = uuid.uuid4().hex
            await _set_state(record.account_id, fresh.state, run_id=run_id)
            _RUNTIME[record.account_id] = asyncio.create_task(
                _warming_loop(record.account_id, run_id=run_id),
            )
            restarted += 1
    if restarted:
        await log_event(
            "INFO",
            "warming_runtime_reconciled",
            extra={"restarted": restarted},
        )
    await _refresh_dialogue_pairs()
    await purge_stale_history()
    _start_purge_task()


async def _purge_loop() -> None:  # pragma: no cover - long-running task body.
    """Rerun the retention sweep every ``purge_interval_hours`` until cancelled.

    ``purge_stale_history`` swallows its own errors, so a failing sweep never
    breaks the cadence. Cancelled cleanly on shutdown like the per-account loops.
    """
    interval = settings.warming.purge_interval_hours * 3600
    while True:
        await asyncio.sleep(interval)
        await purge_stale_history()


def _start_purge_task() -> None:
    """Spawn the periodic retention sweep if one is not already running."""
    global _PURGE_TASK  # noqa: PLW0603 - single process-wide background task handle.
    if _PURGE_TASK is not None and not _PURGE_TASK.done():
        return
    _PURGE_TASK = asyncio.create_task(_purge_loop())


async def _refresh_dialogue_pairs() -> None:
    try:
        await assign_pairs()
    except Exception as exc:  # noqa: BLE001 - reconcile must not fail because dialogues did.
        await log_event(
            "WARNING",
            "warming_dialogue_pair_refresh_failed",
            extra={"error": str(exc)},
        )


async def shutdown_warming_runtime() -> None:
    """Cancel every running loop and wait briefly for graceful exits."""
    await _stop_purge_task()
    if not _RUNTIME:
        return
    tasks = list(_RUNTIME.values())
    _RUNTIME.clear()
    for task in tasks:
        if not task.done():
            task.cancel()
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=settings.warming.stop_cancel_timeout_seconds,
        )
    except TimeoutError:
        await log_event("WARNING", "warming_shutdown_timeout", extra={"count": len(tasks)})


async def _stop_purge_task() -> None:
    """Cancel and await the periodic retention sweep (no-op if not running)."""
    global _PURGE_TASK  # noqa: PLW0603 - single process-wide background task handle.
    task = _PURGE_TASK
    _PURGE_TASK = None
    if task is None or task.done():
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


# The stop/graduation lifecycle lives in ``_graduation`` (file-size budget). It
# imports this module for the shared ``_RUNTIME`` / locks / ``_refresh_dialogue_pairs``
# seam, so the import lands at the bottom — after those are defined — to avoid a
# circular-import cycle. Re-exported so ``services.warming._runtime.<name>`` (and
# the package root) keep resolving these, and tests keep patching seams here.
from services.warming._graduation import (  # noqa: E402, F401 - re-export after globals are defined.
    _stop_warming_locked,
    handoff_to_neurocomment,
    promote_to_neurocomment,
    stop_warming,
    unmark_neurocomment,
)
