"""Stopping a pod after its proxy changed, and removing an account's pod: both bounded and owed.

``mobile_emulator`` passes its own ``stop_pod`` / ``remove_pod`` in, so what the tests (and
the container layer) replace there is what runs here.

* A proxy change waits for an open in flight only ``_LOCK_WAIT_SECONDS``. An open that is
  still going then is MARKED (``_PROXY_CHANGED``): it aborts before it asks Telegram for a
  login code (``refuse_if_proxy_changed``), and the stop runs in a tracked background task
  once the lock frees. A stop that failed is tried again there, a few times with growing
  pauses, and logged once per streak. A pod that is still up after that is caught by
  ``mobile_pods.stop_pods_off_proxy``.
* A removal is a tombstone first (``pending_cleanups``): written BEFORE ``remove_pod`` is
  tried and deleted once it is confirmed (a pod that was never there is confirmed too). A
  removal that failed leaves the row, and ``mobile_pods.retry_pending_pod_removals`` retries
  what the rows name — never what the database lacks.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.config import settings
from core.emulator import EmulatorError
from core.logging import log_event
from core.repositories.pending_cleanups import add_pending_cleanup, clear_pending_cleanup
from services.accounts._mobile_holds import _LOCKS, _OPERATOR, EmulatorRefusedError, container_name

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from schemas.emulator import EmulatorRefusalCode

POD_CLEANUP_KIND = "pod"
_NOT_RUNNING: EmulatorRefusalCode = "emulator_not_running"

# How long a proxy change waits for an open in flight before it marks the open and goes on.
_LOCK_WAIT_SECONDS = 5.0
# Pauses before each retry of a stop that failed: three more tries after the first.
_RETRY_PAUSES_SECONDS = (2.0, 10.0, 30.0)

# Accounts whose proxy changed while an open held the lock: that open must not sign in.
_PROXY_CHANGED: set[str] = set()
# Strong references to the background stops; the event loop holds tasks weakly.
_STOPS: set[asyncio.Task[None]] = set()


def refuse_if_proxy_changed(account_id: str) -> None:
    """Abort an open whose pod's proxy was changed under it, before any login code is asked."""
    if account_id in _PROXY_CHANGED:
        raise EmulatorRefusedError(_NOT_RUNNING)


async def _stop_held(account_id: str, stop: Callable[[str], Awaitable[None]], *, log: bool) -> bool:
    """One stop (the caller holds the account's lock); whether it FAILED (logged when ``log``)."""
    try:
        await stop(container_name(account_id))
    except EmulatorError as exc:
        if log:
            await log_event(
                "WARNING",
                "account_mobile_pod_stop_failed",
                account_id=account_id,
                extra={"reason": exc.code},
            )
        return True
    return False


async def _try_stop(account_id: str, stop: Callable[[str], Awaitable[None]], *, log: bool) -> bool:
    async with _lock(account_id):
        return await _stop_held(account_id, stop, log=log)


def _lock(account_id: str) -> asyncio.Lock:
    return _LOCKS.setdefault(account_id, asyncio.Lock())


async def _stop_in_background(
    account_id: str, stop: Callable[[str], Awaitable[None]], *, after_failure: bool
) -> None:
    """Stop the pod once the lock frees, trying again after a failure.

    ``after_failure``: a stop just failed (and was logged, which is not repeated for this
    streak), so the first pause comes before the first attempt rather than none.
    """
    pauses = _RETRY_PAUSES_SECONDS if after_failure else (0.0, *_RETRY_PAUSES_SECONDS)
    logged = after_failure
    for pause in pauses:
        await asyncio.sleep(pause)
        if not await _try_stop(account_id, stop, log=not logged):
            return
        logged = True


def _schedule(
    account_id: str, stop: Callable[[str], Awaitable[None]], *, after_failure: bool
) -> None:
    task = asyncio.create_task(_stop_in_background(account_id, stop, after_failure=after_failure))
    _STOPS.add(task)
    task.add_done_callback(_STOPS.discard)
    # However it ends (even cancelled before it began), the open it marked is no longer owed.
    task.add_done_callback(lambda _task: _PROXY_CHANGED.discard(account_id))


async def cancel_pending_stops() -> None:
    """Cancel the background stops (at shutdown; the pods are stopped there anyway)."""
    tasks = list(_STOPS)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def stop_after_proxy_change(account_id: str, stop: Callable[[str], Awaitable[None]]) -> None:
    """Stop the account's pod; never fails, and never holds a proxy change up for long."""
    _OPERATOR.discard(account_id)
    lock = _lock(account_id)
    try:
        async with asyncio.timeout(_LOCK_WAIT_SECONDS):
            await lock.acquire()
    except TimeoutError:
        _PROXY_CHANGED.add(account_id)
        _schedule(account_id, stop, after_failure=False)
        return
    try:
        failed = await _stop_held(account_id, stop, log=True)
    finally:
        lock.release()
    if failed:
        _schedule(account_id, stop, after_failure=True)


async def remove_with_tombstone(account_id: str, remove: Callable[[str], Awaitable[bool]]) -> bool:
    """Remove the account's pod (the caller holds its lock); a tombstone stays until it is gone.

    The tombstone is written only with the emulator on (a host without it has no pod to owe)
    and a database that cannot take it never blocks the deletion. Raises the removal's error.
    """
    name = container_name(account_id)
    # The account is going: its "warming must not sign the app in" mark goes with it.
    with suppress(Exception):
        await clear_pending_cleanup("phone_signed_out", account_id)
    tombstoned = settings.emulator.enabled and await _tombstone(account_id, name)
    removed = await remove(name)
    if tombstoned:
        await _clear(account_id, name)
    return removed


async def _tombstone(account_id: str, name: str) -> bool:
    try:
        await add_pending_cleanup(
            POD_CLEANUP_KIND, name, requested_at=datetime.now(UTC).isoformat()
        )
    except Exception as exc:  # noqa: BLE001 - the deletion must not fail because of this
        await log_event(
            "WARNING",
            "account_mobile_tombstone_unwritten",
            account_id=account_id,
            extra={"error_type": type(exc).__name__},
        )
        return False
    return True


async def _clear(account_id: str, name: str) -> None:
    try:
        await clear_pending_cleanup(POD_CLEANUP_KIND, name)
    except Exception as exc:  # noqa: BLE001 - a stale row only costs one more (harmless) retry
        await log_event(
            "WARNING",
            "account_mobile_tombstone_uncleared",
            account_id=account_id,
            extra={"error_type": type(exc).__name__},
        )
