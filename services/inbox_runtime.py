"""Owns all-account Telegram inbox subscriptions for one server process."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_account, list_accounts
from core.logging import log_event
from core.telegram_client import (
    SessionFileMissingError,
    ensure_incoming_message_connection,
    stop_incoming_messages,
    subscribe_incoming_messages,
)
from services.inbox_events import on_message_received

if TYPE_CHECKING:
    from schemas.accounts import AccountRead

logger = logging.getLogger(__name__)

_NON_AUTHORIZED_STATUSES = frozenset(
    {"new", "unauthorized", "session_file_missing", "session_error", "account_error"}
)
_HEALTH_INTERVAL_SECONDS = 30.0
_RUNNING = False
_ACTIVE: set[str] = set()
_RETRIES: dict[str, asyncio.Task[None]] = {}
_MONITORS: dict[str, asyncio.Task[None]] = {}
_STARTUP_TASK: asyncio.Task[None] | None = None
_START_SEMAPHORE = asyncio.Semaphore(3)
# Starts behind one proxy are one at a time with a short random gap, so a fleet that shares a
# proxy never connects in the same instant; accounts on different proxies still overlap.
_START_GAP_SECONDS = (1.0, 3.0)
_PROXY_LOCKS: dict[str, asyncio.Lock] = {}
_NEXT_START_AT: dict[str, float] = {}
# Seam for tests; only pauses and the monitor's jitter are drawn.
_rng = random.Random()  # nosec B311  # noqa: S311 - pacing jitter, not security


async def _pause(seconds: float) -> None:
    await asyncio.sleep(seconds)


async def _connect_behind_proxy(account_id: str, proxy_id: str | None) -> bool:
    """Subscribe ``account_id``, one start at a time per proxy, a random gap after a connect."""
    if proxy_id is None:  # direct: no proxy to share, nothing to serialize.
        async with _START_SEMAPHORE:
            return await subscribe_incoming_messages(account_id, on_message_received)
    async with _PROXY_LOCKS.setdefault(proxy_id, asyncio.Lock()):
        wait = _NEXT_START_AT.get(proxy_id, 0.0) - time.monotonic()
        if wait > 0:
            await _pause(wait)
        # A start slot is taken only for the connection itself: accounts queued behind one
        # proxy's lock and gap must not hold the slots other proxies' accounts could use.
        async with _START_SEMAPHORE:
            subscribed = await subscribe_incoming_messages(account_id, on_message_received)
        if subscribed:
            _NEXT_START_AT[proxy_id] = time.monotonic() + _rng.uniform(*_START_GAP_SECONDS)
        return subscribed


async def _start_account(account_id: str, account: AccountRead | None = None) -> bool:
    if account is None:
        account = await fetch_account(account_id)
    if account is None or account.status in _NON_AUTHORIZED_STATUSES:
        return False
    if not settings.telegram.allow_direct_connection and account.proxy_id is None:
        # Never connected without a proxy (it would use this computer's own IP); not a
        # failure to retry either.
        return False
    subscribed = await _connect_behind_proxy(account_id, getattr(account, "proxy_id", None))
    if subscribed:
        _ACTIVE.add(account_id)
        _schedule_monitor(account_id)
    return subscribed


async def _retry_account(account_id: str) -> None:
    delay = 5.0
    while _RUNNING and account_id not in _ACTIVE:
        await asyncio.sleep(delay)
        try:
            await _start_account(account_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("inbox listener retry failed for %s", account_id)
            await log_event(
                "WARNING",
                "inbox_listener_retry_failed",
                account_id=account_id,
                extra={"error_type": type(exc).__name__},
            )
            delay = min(delay * 2, 60.0)
        else:
            return  # Non-authorized accounts restart when lifecycle reports login.


def _schedule_retry(account_id: str) -> None:
    task = _RETRIES.get(account_id)
    if task is None or task.done():
        task = asyncio.create_task(_retry_account(account_id))
        _RETRIES[account_id] = task

        def clear(done: asyncio.Task[None]) -> None:
            if _RETRIES.get(account_id) is done:
                _RETRIES.pop(account_id, None)

        task.add_done_callback(clear)


def _schedule_monitor(account_id: str) -> None:
    task = _MONITORS.get(account_id)
    if task is None or task.done():
        task = asyncio.create_task(_monitor_account(account_id))
        _MONITORS[account_id] = task

        def clear(done: asyncio.Task[None]) -> None:
            if _MONITORS.get(account_id) is done:
                _MONITORS.pop(account_id, None)

        task.add_done_callback(clear)


async def _monitor_account(account_id: str) -> None:
    delay = _HEALTH_INTERVAL_SECONDS
    outage_logged = False
    while _RUNNING and account_id in _ACTIVE:
        # ±20 % so monitors that woke together (the PC slept) do not reconnect together.
        await asyncio.sleep(delay * _rng.uniform(0.8, 1.2))
        try:
            attached = await ensure_incoming_message_connection(account_id)
            if not attached:
                _ACTIVE.discard(account_id)
                # The monitor registry still points at this live task. Remove it
                # before starting, so _start_account can register its successor.
                if _MONITORS.get(account_id) is asyncio.current_task():
                    _MONITORS.pop(account_id, None)
                await start_account_inbox(account_id)
                return
            delay = _HEALTH_INTERVAL_SECONDS
            outage_logged = False
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not outage_logged:
                logger.exception("inbox listener health check failed for %s", account_id)
                await log_event(
                    "WARNING",
                    "inbox_listener_health_check_failed",
                    account_id=account_id,
                    extra={"error_type": type(exc).__name__},
                )
                outage_logged = True
            delay = min(delay * 2, 300.0)


async def start_account_inbox(account_id: str) -> None:
    """Start one authorized account listener; transient startup faults retry in background."""
    if not _RUNNING or account_id in _ACTIVE:
        return
    try:
        if await _start_account(account_id):
            return
    except SessionFileMissingError:
        # Permanent until the operator restores the file: retrying only fills the log.
        await log_event("WARNING", "account_session_file_missing", account_id=account_id)
    except Exception as exc:
        logger.exception("inbox listener start failed for %s", account_id)
        await log_event(
            "WARNING",
            "inbox_listener_start_failed",
            account_id=account_id,
            extra={"error_type": type(exc).__name__},
        )
        _schedule_retry(account_id)


async def stop_account_inbox(account_id: str, *, forget: bool = False) -> None:
    """Detach before logout/removal; cancelled background tasks cannot reopen the client."""
    tasks = [task for registry in (_RETRIES, _MONITORS) if (task := registry.pop(account_id, None))]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    await stop_incoming_messages(account_id, forget=forget)
    _ACTIVE.discard(account_id)


async def _reconcile_startup() -> None:
    async def start_bounded(account: AccountRead) -> None:
        if account.status in _NON_AUTHORIZED_STATUSES:
            return
        try:
            await _start_account(account.account_id, account)
        except Exception as exc:
            logger.exception("inbox listener startup failed for %s", account.account_id)
            await log_event(
                "WARNING",
                "inbox_listener_start_failed",
                account_id=account.account_id,
                extra={"error_type": type(exc).__name__},
            )
            _schedule_retry(account.account_id)

    while _RUNNING:
        try:
            accounts = await list_accounts()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("inbox startup reconciliation failed")
            await log_event(
                "WARNING",
                "inbox_startup_reconcile_failed",
                extra={"error_type": type(exc).__name__},
            )
            await asyncio.sleep(5)
            continue
        await asyncio.gather(*(start_bounded(account) for account in accounts.accounts))
        return


async def reconcile_inboxes_on_startup() -> None:
    """Start a tracked, bounded account reconciliation without delaying API startup."""
    global _RUNNING, _STARTUP_TASK  # noqa: PLW0603
    _RUNNING = True
    if _STARTUP_TASK is None or _STARTUP_TASK.done():
        _STARTUP_TASK = asyncio.create_task(_reconcile_startup())


async def shutdown_inbox_runtime() -> None:
    """Stop retries, health checks and handlers before the Telegram pool closes."""
    global _RUNNING, _STARTUP_TASK  # noqa: PLW0603
    _RUNNING = False
    startup = _STARTUP_TASK
    _STARTUP_TASK = None
    tasks = list(_RETRIES.values()) + list(_MONITORS.values())
    _RETRIES.clear()
    _MONITORS.clear()
    if startup is not None:
        tasks.append(startup)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    account_ids = list(_ACTIVE)
    await asyncio.gather(*(stop_incoming_messages(account_id) for account_id in account_ids))
    _ACTIVE.clear()


def _reset_for_tests() -> None:
    """Reset process-local runtime state between tests."""
    global _RUNNING, _STARTUP_TASK, _START_SEMAPHORE  # noqa: PLW0603
    _RUNNING = False
    _ACTIVE.clear()
    _RETRIES.clear()
    _MONITORS.clear()
    _STARTUP_TASK = None
    _START_SEMAPHORE = asyncio.Semaphore(3)
    _PROXY_LOCKS.clear()
    _NEXT_START_AT.clear()
