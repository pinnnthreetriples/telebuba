"""The Telethon client every Telebuba flow builds: a request never ends in a stray cancel.

Why: when a client's update loop meets a dead key (``UnauthorizedError`` / ``AuthKeyError``
while fetching the difference) Telethon calls ``disconnect()`` itself
(``telethon/client/updates.py``), and a disconnect without an error ``cancel()``s every
request still waiting for its answer (``telethon/network/mtprotosender.py``). Each caller
then receives a ``CancelledError`` that nobody asked for. Code that rightly lets
cancellation through (``except Exception``) was ended by it: on 2026-11-14 the session
guard went silent for 41 hours this way, right as an account's key died, and the death
itself was never classified.

So a request whose own task is not being cancelled (``Task.cancelling() == 0``) and still
ends in ``CancelledError`` raises what actually happened instead: the auth error Telethon
recorded when its update loop stopped (``_updates_error``), which every caller already
classifies as a dead session, or :class:`TelegramClientDisconnectedError`, a
``ConnectionError`` the callers already treat as an unreachable account. A real
cancellation (shutdown, a deadline) propagates untouched.
"""

from __future__ import annotations

import asyncio
from typing import Any

from telethon import TelegramClient, errors

__all__ = ["GuardedTelegramClient", "TelegramClientDisconnectedError"]


class TelegramClientDisconnectedError(ConnectionError):
    """The client was disconnected under a request that was still waiting for its answer."""


def _own_cancellation() -> bool:
    task = asyncio.current_task()
    return task is None or task.cancelling() > 0


def disconnect_reason(client: TelegramClient) -> Exception:
    """What ended the client's connection under a pending request, as the caller should see it."""
    recorded = getattr(client, "_updates_error", None)
    if isinstance(recorded, (errors.UnauthorizedError, errors.AuthKeyError)):
        return recorded
    return TelegramClientDisconnectedError("the client disconnected under a pending request")


class GuardedTelegramClient(TelegramClient):
    """``TelegramClient`` whose requests never surface Telethon's own cancellation."""

    async def _call(
        self,
        sender: Any,  # noqa: ANN401 - Telethon's own untyped signature
        request: Any,  # noqa: ANN401
        ordered: bool = False,  # noqa: FBT001, FBT002 - Telethon's own signature
        flood_sleep_threshold: int | None = None,
    ) -> Any:  # noqa: ANN401
        try:
            return await super()._call(
                sender, request, ordered=ordered, flood_sleep_threshold=flood_sleep_threshold
            )
        except asyncio.CancelledError:
            if _own_cancellation():
                raise
            raise disconnect_reason(self) from None
