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
    """How many registered windows still have a running browser behind them."""
    async with web_login._windows_lock:  # noqa: SLF001 - registry owned by web_login
        return sum(1 for window in web_login._windows.values() if window.alive)  # noqa: SLF001


async def close_all_web_windows() -> int:
    """Kill every registered window and its relay; return how many were still on screen.

    The registry lock is held only to read, as in ``shutdown_web_login``. Each window
    then goes through the path that ends an operator-closed one: under its account's
    open lock, so an open in flight finishes first and a window it replaced is left
    alone. Concurrently, so one account mid-login does not hold up the rest.
    """
    async with web_login._windows_lock:  # noqa: SLF001 - registry owned by web_login
        registered = list(web_login._windows.items())  # noqa: SLF001
    on_screen = sum(1 for _account_id, window in registered if window.alive)
    await asyncio.gather(
        *(web_login._window_closed(account_id, window) for account_id, window in registered)  # noqa: SLF001
    )
    return on_screen
