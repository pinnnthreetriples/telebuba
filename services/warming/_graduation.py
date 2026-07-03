"""Warming stop + graduation lifecycle — the "stop and beyond" paths.

``stop_warming`` / ``_stop_warming_locked`` cancel a running loop and return the
account to idle; ``promote_to_neurocomment`` / ``unmark_neurocomment`` graduate
an account into (or back out of) the neurocomment pool. Split from ``_runtime``
for the file-size budget; ``_runtime`` re-imports every name so call sites keep
importing them from ``services.warming._runtime`` (and the package root).

The shared runtime dict / per-account locks are imported by name (tests mutate,
never reassign, ``_runtime._RUNTIME``, so the binding stays valid). The patchable
``_refresh_dialogue_pairs`` seam is reached through the ``_runtime`` module object
so a ``monkeypatch`` on ``services.warming._runtime._refresh_dialogue_pairs``
still takes effect here.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    fetch_account,
    mark_promoted_to_nc,
    unmark_promoted_to_nc,
)
from core.logging import log_event
from services.warming import _runtime
from services.warming._runtime import _RUNTIME, _account_lock
from services.warming._state import _current_card, _set_state
from services.warming.pacing import _now_iso

if TYPE_CHECKING:
    from schemas.warming import (
        StopWarmingRequest,
        WarmingAccountState,
    )


async def _stop_warming_locked(account_id: str) -> None:
    """Inner stop, run with ``_account_lock(account_id)`` already held.

    Extracted so service-level operations that need to compose stop with
    other state mutations (e.g. ``remove_account``) can hold the lock across
    both steps. See P2.2.
    """
    task = _RUNTIME.pop(account_id, None)
    if task is not None and not task.done():
        task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=settings.warming.stop_cancel_timeout_seconds,
            )
        except (TimeoutError, asyncio.CancelledError):
            # Either we timed out or the cancel propagated correctly —
            # in both cases the task is no longer ours to await.
            pass
        except Exception as exc:  # noqa: BLE001 - log+continue; stop must not fail.
            await log_event(
                "WARNING",
                "warming_stop_task_error",
                account_id=account_id,
                extra={"error_type": type(exc).__name__, "message": str(exc)},
            )
    account = await fetch_account(account_id)
    if account is not None:
        # Round-4 P1.1: clear run_id when stopping so the row carries no live
        # generation. A stale loop's CAS write that targets the previous
        # run_id therefore cannot match (its WHERE turns the UPDATE into a
        # no-op). Belt; the CAS-rejects-idle clause in _upsert_warming_state
        # is the suspenders.
        await _set_state(
            account_id,
            "idle",
            last_event="stopped",
            stopped_at=_now_iso(),
            run_id=None,
        )


async def stop_warming(data: StopWarmingRequest) -> WarmingAccountState:
    """Cancel an account's loop task and return it to the idle column.

    Awaits the task with a timeout so callers get back a settled state — a UI
    poll that re-reads the board will see a real ``idle`` row, not a still-
    running shadow loop. Stopping a ghost account (no row in ``accounts``) is
    a no-op for the DB — only the in-memory task is cleaned up.
    """
    async with _account_lock(data.account_id):
        await _stop_warming_locked(data.account_id)
    await log_event("INFO", "warming_stopped", account_id=data.account_id)
    await _runtime._refresh_dialogue_pairs()  # noqa: SLF001 - patchable seam, reached via the module.
    return await _current_card(data.account_id)


async def promote_to_neurocomment(account_id: str) -> WarmingAccountState:
    """Graduate an account: stop its warming loop and flag it for the neurocomment pool.

    Two-step operation under one lock so we don't race a freshly-restarted loop:
    cancel any running task, then persist ``promoted_to_nc=True``. The card the
    caller re-renders shows the account in idle with the flag set, and the
    neurocomment warmed-account overview will pick it up on the next poll.
    """
    async with _account_lock(account_id):
        await _stop_warming_locked(account_id)
        await mark_promoted_to_nc(account_id)
    await log_event("INFO", "warming_promoted_to_neurocomment", account_id=account_id)
    await _runtime._refresh_dialogue_pairs()  # noqa: SLF001 - patchable seam, reached via the module.
    return await _current_card(account_id)


async def unmark_neurocomment(account_id: str) -> WarmingAccountState:
    """Reverse a graduation: clear ``promoted_to_nc`` (Group C un-promote button).

    Held under the per-account lock for symmetry with ``promote_to_neurocomment``
    so a concurrent re-promote / restart does not race the flip.
    """
    async with _account_lock(account_id):
        await unmark_promoted_to_nc(account_id)
    await log_event("INFO", "warming_unpromoted_from_neurocomment", account_id=account_id)
    return await _current_card(account_id)
