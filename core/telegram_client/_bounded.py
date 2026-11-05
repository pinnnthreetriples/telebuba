"""Await something for a bounded time, then cancel it and give it a bounded moment to unwind."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable

__all__ = ["await_bounded"]


async def await_bounded(pending: Awaitable[object], timeout: float, grace: float) -> None:  # noqa: ASYNC109 - the deadline is the point
    """Await ``pending`` for ``timeout`` seconds, else cancel it and wait at most ``grace`` more.

    Unlike ``asyncio.wait_for`` the caller is never held by a task that swallows its
    cancellation: after ``grace`` it is abandoned and ``TimeoutError`` is raised anyway.
    """
    task = asyncio.ensure_future(pending)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout)
    except TimeoutError:
        task.cancel()
        await asyncio.wait({task}, timeout=grace)
        raise
