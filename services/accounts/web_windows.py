"""Counting and closing every open web.telegram.org window at once.

Each globe click keeps a full Chrome process per account, so the Accounts page warns
when many are up and offers to end them all. Split out of
:mod:`services.accounts.web_login` for the file-size budget; the registry, its lock
and the kill path stay owned there and are read through the module at call time.

Closing is NOT shutdown: ``_closing`` is left alone, so a later click opens normally,
and the signed-in profile markers are never touched, so a window closed here reopens
already signed in.
"""

from __future__ import annotations

import asyncio

from services.accounts import web_login

__all__ = ["close_all_web_windows", "count_open_web_windows"]


async def count_open_web_windows() -> int:
    """How many registered windows still have a running browser behind them.

    ``running``, not ``alive``: a window whose socket dropped is still a Chrome process,
    and nothing else ends it — the close watcher waits on the process.
    """
    async with web_login._windows_lock:  # noqa: SLF001 - registry owned by web_login
        return sum(1 for window in web_login._windows.values() if window.running)  # noqa: SLF001


async def close_all_web_windows() -> int:
    """Kill every registered window and its relay; return how many were still on screen.

    The registry lock is held only to list the accounts, as in ``shutdown_web_login``.
    Concurrently, so one account mid-login does not hold up the rest.
    """
    async with web_login._windows_lock:  # noqa: SLF001 - registry owned by web_login
        account_ids = list(web_login._windows)  # noqa: SLF001
    closed = await asyncio.gather(
        *(_close_current_window(account_id) for account_id in account_ids)
    )
    return sum(closed)


async def _close_current_window(account_id: str) -> int:
    """End the window the account holds once its open lock is free; 1 if it was running.

    Under the open lock, as ``_drop_window`` requires: an open in flight finishes first,
    and the window read AFTER it is the one closed — a relaunch it made, not the stale
    one it replaced.
    """
    async with await web_login._account_open_lock(account_id):  # noqa: SLF001
        async with web_login._windows_lock:  # noqa: SLF001
            window = web_login._windows.get(account_id)  # noqa: SLF001
        if window is None:
            return 0
        running = window.running
        await web_login._drop_window(account_id, window)  # noqa: SLF001
    return int(running)
