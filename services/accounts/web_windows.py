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

from services.accounts import web_login

__all__ = ["close_all_web_windows", "count_open_web_windows"]


async def count_open_web_windows() -> int:
    """How many registered windows still have a running browser behind them."""
    async with web_login._windows_lock:  # noqa: SLF001 - registry owned by web_login
        return sum(1 for window in web_login._windows.values() if window.alive)  # noqa: SLF001


async def close_all_web_windows() -> int:
    """Kill every registered window; return how many were still on screen.

    The same discipline as ``shutdown_web_login``: the registry lock is held only to
    read, and each kill runs outside it through the one path that deregisters and
    ends a window. A window an open registers after the read is left alone.
    """
    async with web_login._windows_lock:  # noqa: SLF001 - registry owned by web_login
        registered = list(web_login._windows.items())  # noqa: SLF001
    on_screen = sum(1 for _account_id, window in registered if window.alive)
    for account_id, window in registered:
        await web_login._drop_window(account_id, window)  # noqa: SLF001
    return on_screen
