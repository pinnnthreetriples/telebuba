"""Operator-bot runtime — turn selected log events into Telegram messages.

One in-process task reads the live log bus (``core.events.subscribe``, the same fan-out
the SSE stream reads), maps each row to a notification kind, and sends at most one
message per ``(account, kind)`` every ``settings.notifications.throttle_hours``. The
throttle is in memory, which is enough with the one uvicorn worker this app runs.

Nothing here may break the event pipeline: a send that fails is logged by status and
exception class only (the token sits in the Bot API URL), and any other failure in
handling one row is caught so the next row is still read. The throttle is stamped
when a send is attempted, delivered or not, so an unreachable Telegram costs one
warning per window instead of one per event.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_account
from core.events import subscribe
from core.logging import log_event
from core.notify_bot import send_bot_message
from core.repositories.notification_settings import load_notification_settings
from schemas.notifications import BotMessage, NotificationKind
from services._notification_messages import account_label, compose_message, notification_kind

if TYPE_CHECKING:
    from schemas.logs import LogEntry

# Stdlib sink for the class of an unexpected handler failure — see ``main``.
logger = logging.getLogger(__name__)

_SECONDS_PER_HOUR = 3600

_ThrottleKey = tuple[str | None, NotificationKind]


class _Runtime:
    task: asyncio.Task[None] | None = None
    # Monotonic time of the last attempted send per (account, kind).
    last_sent: dict[_ThrottleKey, float] = {}  # noqa: RUF012 - one process-wide map.
    clock = staticmethod(time.monotonic)


_runtime = _Runtime()


def reset_for_tests() -> None:
    """Forget the throttle and any task reference (tests only)."""
    _runtime.task = None
    _runtime.last_sent.clear()


def _claim_slot(key: _ThrottleKey) -> bool:
    """Whether ``key`` may send now; stamps the slot when it may."""
    now = _runtime.clock()
    window = settings.notifications.throttle_hours * _SECONDS_PER_HOUR
    last = _runtime.last_sent.get(key)
    if last is not None and now - last < window:
        return False
    _runtime.last_sent[key] = now
    return True


async def _label(account_id: str | None) -> str:
    account = await fetch_account(account_id) if account_id else None
    return account_label(account)


async def handle_log_entry(entry: LogEntry) -> None:
    """Send the notification ``entry`` asks for, if any, enabled and not throttled."""
    kind = notification_kind(entry)
    if kind is None:
        return
    config = await load_notification_settings()
    if not (config.enabled and config.bot_token and config.chat_id):
        return
    if not _claim_slot((entry.account_id, kind)):
        return
    text = compose_message(kind, await _label(entry.account_id))
    result = await send_bot_message(
        BotMessage(bot_token=config.bot_token, chat_id=config.chat_id, text=text),
    )
    if result.status != "sent":
        await log_event(
            "WARNING",
            "notifications_send_failed",
            account_id=entry.account_id,
            extra={"kind": kind, "status": result.status, "error_type": result.error_type},
        )


async def _consume(queue: asyncio.Queue[LogEntry]) -> None:
    while True:
        entry = await queue.get()
        try:
            await handle_log_entry(entry)
        except Exception as exc:  # noqa: BLE001 - one bad row must never stop the bot
            logger.warning("notification handling failed: %s", type(exc).__name__)


async def _run(ready: asyncio.Event) -> None:
    async with subscribe() as queue:
        ready.set()
        await _consume(queue)


async def start_notifications() -> None:
    """Subscribe to the log bus; returns once the subscription is live."""
    if _runtime.task is not None and not _runtime.task.done():
        return
    ready = asyncio.Event()
    _runtime.task = asyncio.create_task(_run(ready), name="operator-notifications")
    await ready.wait()


async def shutdown_notifications() -> None:
    """Cancel the subscriber task and wait for it to leave the bus."""
    task, _runtime.task = _runtime.task, None
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
