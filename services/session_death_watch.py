"""Notice a dead key the moment anything trips over it, and check the session at once.

Why: an action that meets a dead key (warming, a campaign, a broadcast, a read) is told
``session_dead`` but deliberately writes no status (``core.telegram_client._actions``),
and the session watch reads only accounts that nothing else is using. So an account busy
with warming could die and stay ``alive`` for days, with nothing journalled and nobody
told: on 2026-11-14 a key died and was found 41 hours later.

One in-process task reads the live log bus (like the operator bot). A row saying an
account's key is dead (an action's ``*_failed`` with ``error_type`` ``session_dead`` /
``account_deactivated``, or a session audit refused with that code) starts one ordinary
session check for that account (``check_account_session``): it records the status,
journals the death once and, through the journal, tells the operator bot. Per account
at most once per ``SESSION_AUDIT__DEATH_CHECK_COOLDOWN_SECONDS``.

Never for an account without a proxy: a check connects, and a proxyless client would
connect from the operator's own IP. Never for one already recorded dead. One uvicorn
worker, so the memory of recent checks is process-wide and enough.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_account
from core.events import subscribe
from core.logging import log_event
from schemas.accounts import AccountCheckRequest
from services.accounts import check_account_session
from services.lifecycle_journal import DEAD_SESSION_STATUSES

if TYPE_CHECKING:
    from schemas.logs import LogEntry

__all__ = [
    "death_signal",
    "reset_for_tests",
    "shutdown_session_death_watch",
    "start_session_death_watch",
]

logger = logging.getLogger(__name__)

_DEATH_CODES = frozenset({"session_dead", "account_deactivated"})
# Refusals that carry the code under ``code`` rather than an action's ``error_type``.
_CODED_EVENTS = frozenset({"session_audit_failed", "own_authorizations_failed"})


@dataclass
class _State:
    task: asyncio.Task[None] | None = None
    pending: set[asyncio.Task[None]] = field(default_factory=set)
    # When each account's check was last started (``time.monotonic``).
    checked: dict[str, float] = field(default_factory=dict)


_state = _State()


def death_signal(entry: LogEntry) -> str | None:
    """The account whose key ``entry`` says is dead, or ``None``."""
    if entry.account_id is None:
        return None
    if entry.event.endswith("_failed") and entry.extra.get("error_type") in _DEATH_CODES:
        return entry.account_id
    if entry.event in _CODED_EVENTS and entry.extra.get("code") in _DEATH_CODES:
        return entry.account_id
    return None


def _due(account_id: str) -> bool:
    last = _state.checked.get(account_id)
    cooldown = settings.session_audit.death_check_cooldown_seconds
    return last is None or time.monotonic() - last >= cooldown


async def _check(account_id: str, source: str) -> None:
    account = await fetch_account(account_id)
    if account is None or account.proxy_id is None or account.status in DEAD_SESSION_STATUSES:
        return
    await log_event(
        "WARNING", "session_death_suspected", account_id=account_id, extra={"source": source}
    )
    try:
        await check_account_session(AccountCheckRequest(account_id=account_id))
    except Exception as exc:  # noqa: BLE001 - one failed check must not stop the watch.
        logger.warning("death check failed for an account: %s", type(exc).__name__)


def _schedule(entry: LogEntry) -> None:
    account_id = death_signal(entry)
    if account_id is None or not _due(account_id):
        return
    _state.checked[account_id] = time.monotonic()
    task = asyncio.create_task(_check(account_id, entry.event))
    _state.pending.add(task)
    task.add_done_callback(_state.pending.discard)


async def _run(ready: asyncio.Event) -> None:
    async with subscribe() as queue:
        ready.set()
        while True:
            _schedule(await queue.get())


async def start_session_death_watch() -> None:
    """Subscribe to the log bus (lifespan startup); returns once the subscription is live."""
    if _state.task is not None and not _state.task.done():
        return
    ready = asyncio.Event()
    _state.task = asyncio.create_task(_run(ready), name="session-death-watch")
    await ready.wait()


async def shutdown_session_death_watch() -> None:
    """Leave the bus and cancel checks in flight (before the client pool goes)."""
    task, _state.task = _state.task, None
    pending = [task, *_state.pending] if task is not None else list(_state.pending)
    for running in pending:
        running.cancel()
    for running in pending:
        with contextlib.suppress(asyncio.CancelledError):
            await running


def reset_for_tests() -> None:
    """Forget the task and the recent checks between tests."""
    global _state  # noqa: PLW0603 - one module-level runtime, reset between tests
    _state = _State()
