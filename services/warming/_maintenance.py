"""Runtime reconciliation, retention sweep, and bounded shutdown."""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from schemas.warming import is_warming
from services.warming import _runtime

if TYPE_CHECKING:
    from schemas.warming import WarmingSettingsSecret, WarmingStateRecord


async def reconcile_warming_runtime() -> None:
    """Restore eligible persisted warming loops after a process restart."""
    records = await _runtime._list_runtime_states()  # noqa: SLF001 - repository seam.
    controls = await _runtime.load_warming_settings()
    channel_count = len((await _runtime.list_warming_channels()).channels)
    restarted = 0
    for record in records:
        if _is_interrupted_stop(record):
            await _settle_interrupted_stop(record)
            continue
        if not is_warming(record.state) or record.state == "error":
            continue
        restarted += await _reconcile_account(record.account_id, controls, channel_count)
    if restarted:
        await log_event("INFO", "warming_runtime_reconciled", extra={"restarted": restarted})
    await _runtime._refresh_dialogue_pairs()  # noqa: SLF001 - patchable runtime seam.
    await _runtime._purge_runtime_history()  # noqa: SLF001 - retention seam.
    _start_purge_task()


def _is_interrupted_stop(record: WarmingStateRecord) -> bool:
    """True for a row a Stop parked and then never came back to.

    ``_cancel_runtime_task`` writes ``error`` plus a ``stopping-`` generation marker
    BEFORE it cancels, so no stale CAS can land while the coroutine unwinds, and the
    stop path rewrites the row the moment the wait resolves. A marker still on the row
    in a process that has only just started therefore means the previous process died
    inside that window — nothing in this one can be stopping anything yet.
    """
    return record.state == "error" and (record.run_id or "").startswith(
        _runtime._STOPPING_MARKER_PREFIX  # noqa: SLF001 - shared marker vocabulary.
    )


async def _settle_interrupted_stop(record: WarmingStateRecord) -> None:
    """Finish the Stop the previous process was killed in the middle of.

    Left alone the row keeps a state ``reconcile_warming_runtime`` deliberately refuses
    to resurrect, so the account quietly stops warming, and the reason it shows the
    operator — "warming task is stopping" — names a coroutine that no longer exists in
    any process. Both halves are wrong to leave: the silence is how a fleet loses an
    account for days, and a lie on the card is how an operator learns not to read it.

    Idle, not the state the row held before the stop: the operator had asked for a stop
    (or for the restart that cancels first), and idle is where both were heading. From
    there a Start is the same single click it is for any other idle account, which is
    also the recovery for a restart that never got to publish its new generation.
    The CAS is the marker itself — if anything in this process has already taken the
    row, this write must not land on top of it.
    """
    marker = record.run_id
    await _runtime._set_state(  # noqa: SLF001 - patchable state seam.
        record.account_id,
        "idle",
        last_event="stopped_after_restart",
        last_error=None,
        stopped_at=_runtime._now_iso(),  # noqa: SLF001 - patchable time seam.
        run_id=None,
        expected_run_id=marker,
    )
    await log_event(
        "WARNING",
        "warming_stop_interrupted",
        account_id=record.account_id,
    )


async def _reconcile_account(
    account_id: str,
    controls: WarmingSettingsSecret,
    channel_count: int,
) -> int:
    """Restore one account if its locked, freshly-read state remains eligible."""
    async with _runtime._account_lock(account_id):  # noqa: SLF001 - shared lifecycle lock.
        fresh = await _runtime.fetch_warming_state(account_id)
        if fresh is None or not is_warming(fresh.state) or fresh.state == "error":
            return 0
        existing = _runtime._RUNTIME.get(account_id)  # noqa: SLF001 - shared ownership map.
        if existing is not None and not existing.done():
            return 0
        account = await _runtime.fetch_account(account_id)
        if account is None:
            await _runtime._set_state(  # noqa: SLF001 - patchable state seam.
                account_id,
                "idle",
                last_event="reconcile_orphan",
                stopped_at=_runtime._now_iso(),  # noqa: SLF001 - patchable time seam.
            )
            return 0
        if controls.enforce_readiness and fresh.state in ("active", "sleeping"):
            ready = await _runtime._evaluate_account_readiness(  # noqa: SLF001
                account_id,
                account,
                channel_count,
            )
            if not ready.ready:
                await _park_not_ready(account_id, ready.reasons)
                return 0
        run_id = uuid.uuid4().hex
        await _runtime._set_state(account_id, fresh.state, run_id=run_id)  # noqa: SLF001
        _runtime._spawn_runtime_task(account_id, run_id)  # noqa: SLF001
        return 1


async def _park_not_ready(account_id: str, reasons: list[str]) -> None:
    await _runtime._set_state(  # noqa: SLF001 - patchable state seam.
        account_id,
        "error",
        last_event="reconcile_not_ready",
        last_error="; ".join(reasons),
        heartbeat_at=_runtime._now_iso(),  # noqa: SLF001 - patchable time seam.
    )
    await log_event(
        "WARNING",
        "warming_reconcile_not_ready",
        account_id=account_id,
        extra={"reasons": reasons},
    )


async def _purge_loop() -> None:  # pragma: no cover - long-running task body.
    interval = settings.warming.purge_interval_hours * 3600
    while True:
        await asyncio.sleep(interval)
        await _runtime._purge_runtime_history()  # noqa: SLF001 - retention seam.
        await _runtime._refresh_dialogue_pairs()  # noqa: SLF001 - patchable runtime seam.


def _start_purge_task() -> None:
    """Spawn the retention sweep if one is not already running."""
    task = _runtime._PURGE_TASK  # noqa: SLF001 - shared task handle.
    if task is not None and not task.done():
        return
    _runtime._PURGE_TASK = asyncio.create_task(_purge_loop())  # noqa: SLF001


async def shutdown_warming_runtime() -> None:
    """Cancel every owned loop and wait only for the configured stop budget."""
    await _stop_purge_task()
    if not _runtime._RUNTIME:  # noqa: SLF001 - shared ownership map.
        return
    owned = list(_runtime._RUNTIME.items())  # noqa: SLF001
    for account_id, task in owned:
        _runtime._seams.revoke_lease(account_id)  # noqa: SLF001 - runtime lease seam.
        if not task.done():
            task.cancel()
    done, pending = await asyncio.wait(
        {task for _, task in owned},
        timeout=settings.warming.stop_cancel_timeout_seconds,
    )
    for account_id, task in owned:
        if task in done:
            _runtime._discard_runtime_task(account_id, task)  # noqa: SLF001
    if pending:
        await log_event("WARNING", "warming_shutdown_timeout", extra={"count": len(pending)})


async def _stop_purge_task() -> None:
    """Cancel and bounded-wait for the retention sweep, releasing the handle either way.

    Unlike a per-account loop, a sweep that outlives its cancellation is not something
    ``_start_purge_task`` may refuse to work around: it reads ``not task.done()`` as
    "one is already running" and skips, so a single stuck sweep switched retention off
    for the rest of the process lifetime and the append-only tables — the exact growth
    the periodic sweep exists to bound — grew unwatched and silently. Dropping the
    handle costs at most a duplicate sweep, and the sweep only re-runs deletes that are
    idempotent by construction; keeping it costs retention entirely.

    There is no ownership to protect here either, which is what makes this safe and the
    account loops different: a sweep touches no Telegram session, so a second one
    cannot overlap the first the way two warming generations would.
    """
    task = _runtime._PURGE_TASK  # noqa: SLF001 - shared task handle.
    _runtime._PURGE_TASK = None  # noqa: SLF001
    if task is None or task.done():
        return
    task.cancel()
    done, _pending = await asyncio.wait(
        {task},
        timeout=settings.warming.stop_cancel_timeout_seconds,
    )
    if not done:
        await log_event("WARNING", "warming_purge_stop_timeout")
