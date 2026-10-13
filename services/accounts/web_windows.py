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
from contextlib import suppress

from core.logging import log_event
from core.web_login import (
    account_profile_dir,
    close_orphaned_browsers,
    remove_profile_dir,
    web_profiles_root,
)
from services.accounts import web_login

__all__ = [
    "close_all_web_windows",
    "close_orphaned_web_windows",
    "count_open_web_windows",
    "forget_account_web",
]


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


async def close_orphaned_web_windows() -> int:
    """Close the windows a previous backend run left open; startup only.

    Before any window of this run exists, so nothing found is one of ours. Such a window
    has no DevTools client — no fingerprint after its next reload — and still claims its
    profile, so the account's next open would fail as a hand-off.
    """
    return await close_orphaned_browsers(web_profiles_root())


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


async def forget_account_web(account_id: str) -> None:
    """End a deleted account's window and relay, then delete its browser profile.

    The profile holds a signed-in Telegram Web session, and a window left open keeps
    using it, so neither may outlive the account. Best effort and never raising: the
    account is already gone, and a failure here is logged by code rather than undoing
    or blocking the delete. Under the open lock, as ``_drop_window`` requires; the lock
    is dropped afterwards, since nothing else ever forgets a deleted account's key.
    """
    try:
        async with await web_login._account_open_lock(account_id):  # noqa: SLF001
            await web_login._kill_registered_window(account_id)  # noqa: SLF001
            # A relay can be up with no window registered (an open that failed after it).
            async with web_login._relays_lock:  # noqa: SLF001
                entry = web_login._relays.pop(account_id, None)  # noqa: SLF001
            if entry is not None:
                with suppress(Exception):
                    await entry[0].aclose()
            await remove_profile_dir(account_profile_dir(account_id))
    except Exception as exc:  # noqa: BLE001 - the delete must not fail because of this
        await log_event(
            "WARNING",
            "account_remove_web_cleanup_failed",
            account_id=account_id,
            extra={"error_type": type(exc).__name__},
        )
    async with web_login._open_locks_guard:  # noqa: SLF001
        web_login._open_locks.pop(account_id, None)  # noqa: SLF001
