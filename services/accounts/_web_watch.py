"""Notice when the operator closes an account's web window, and run its cleanup.

One small task per registered window waits for the browser process to exit, then runs
the cleanup ``web_login`` hands it. The tasks are held here — a bare ``create_task``
can be garbage-collected mid-wait — and :func:`cancel_watchers` ends them on shutdown.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from core.web_login.browser import WebWindow

_watchers: set[asyncio.Task[None]] = set()


def watch_window(window: WebWindow, on_closed: Callable[[], Awaitable[None]]) -> None:
    """Run ``on_closed`` once ``window``'s browser process has exited."""
    task = asyncio.create_task(_watch(window, on_closed))
    _watchers.add(task)
    task.add_done_callback(_watchers.discard)


async def _watch(window: WebWindow, on_closed: Callable[[], Awaitable[None]]) -> None:
    """Wait for the browser to exit OR its DevTools socket to end, then run the cleanup.

    The second trigger matters: Chrome strips every fingerprint override the moment the
    socket drops, so a browser that outlives its socket is a window the operator keeps
    using while it shows Telegram their real machine. ``on_closed`` kills the browser, so
    "socket gone, Chrome alive" ends the same way "Chrome gone" does.
    """
    exited = asyncio.ensure_future(window.wait_closed())
    detached = asyncio.ensure_future(window.session.wait_closed())
    try:
        await asyncio.wait({exited, detached}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (exited, detached):
            task.cancel()
    await on_closed()


async def cancel_watchers() -> None:
    """Cancel every watcher and wait for them to finish."""
    tasks = list(_watchers)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
