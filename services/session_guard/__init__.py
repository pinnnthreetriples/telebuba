"""Session guard: the background work that keeps an account's sessions ours.

It ends the old authorization of a re-keyed account once Telegram lets it (when the
setting allows), and re-reads each alive account's sessions every few hours (the watch);
see ``_loop`` and ``_watch``. A watchdog (``_watchdog``) reports and repairs a guard that
stalls; :func:`read_session_guard_status` is its heartbeat for the API. The death watch
(``services.session_death_watch``) starts and stops with it: both keep an account's key from
dying unseen.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from services.session_backups import shutdown_session_backups, start_session_backups
from services.session_death_watch import shutdown_session_death_watch, start_session_death_watch
from services.session_guard import _loop
from services.session_guard._loop import guard_status, run_guard_pass
from services.session_guard._watchdog import start_watchdog, stop_watchdog

if TYPE_CHECKING:
    from schemas.session_guard import SessionGuardStatus

__all__ = [
    "read_session_guard_status",
    "run_guard_pass",
    "shutdown_session_guard",
    "start_session_guard",
]


async def start_session_guard() -> None:
    """Start the guard, its watchdog and the death watch (lifespan startup)."""
    await _loop.start_session_guard()
    start_watchdog()
    await start_session_death_watch()
    await start_session_backups()


async def shutdown_session_guard() -> None:
    """Stop the watchdog first (so it does not restart the guard), the guard, the death watch.

    Before the client pool: a step or a check in flight borrows a pooled client.
    """
    await stop_watchdog()
    await _loop.shutdown_session_guard()
    await shutdown_session_death_watch()
    await shutdown_session_backups()


async def read_session_guard_status() -> SessionGuardStatus:
    """Whether the guard runs, when it last did something, and whether it has stalled."""
    return guard_status()
