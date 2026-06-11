"""Three-tier logging gateway.

Single entry point for all logging across the project. Per non-negotiable #4
nothing else imports ``loguru`` or ``sentry_sdk`` — features and services
exclusively call :func:`log_event`.

Tiers (per ``context/logging.md``):

1. **loguru** rotating ``debug.log`` — diagnostic noise (stacktraces, retries,
   timings). Always on.
2. **SQLite ``logs`` table** via ``core.db.insert_log_row`` — structured
   business events with normalised ``status`` (success/warning/error). Drives
   the future Logs page.
3. **Sentry** — only ``ERROR`` events, only when ``SENTRY_DSN`` is configured.
   Skipped otherwise; nothing is sent in dev.

``setup_logging()`` is idempotent: safe to call multiple times (only the first
call performs side effects). ``main.py`` calls it once at startup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sentry_sdk
from loguru import logger

from core.config import settings
from core.db import insert_log_row
from schemas.logs import LogEventInput

if TYPE_CHECKING:
    from schemas.logs import LogLevel


class _State:
    initialized: bool = False
    sentry_active: bool = False


_state = _State()


def setup_logging() -> None:
    """Configure loguru sink and Sentry. Idempotent."""
    if _state.initialized:
        return

    logger.remove()  # drop loguru's default stderr sink
    logger.add(
        settings.logging.path,
        rotation=settings.logging.rotation,
        retention=settings.logging.retention,
        level=settings.logging.level,
        enqueue=True,
        backtrace=True,
        diagnose=False,  # avoid leaking variable values into the file
    )

    if settings.logging.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.logging.sentry_dsn,
            traces_sample_rate=0.0,
            send_default_pii=False,
        )
        _state.sentry_active = True
    else:
        _state.sentry_active = False

    _state.initialized = True


def reset_logging_for_tests() -> None:
    """Drop all loguru sinks and reset module state. For tests only."""
    logger.remove()
    _state.initialized = False
    _state.sentry_active = False


_LOG_METHODS = {
    "INFO": logger.info,
    "WARNING": logger.warning,
    "ERROR": logger.error,
}


def _send_to_sentry(event: LogEventInput) -> None:
    if not _state.sentry_active:
        return
    if event.level != "ERROR":
        return
    with sentry_sdk.push_scope() as scope:
        scope.set_tag("event", event.event)
        if event.account_id is not None:
            scope.set_tag("account_id", event.account_id)
        for key, value in event.extra.items():
            scope.set_extra(key, value)
        message = (
            f"{event.event} (account_id={event.account_id})"
            if event.account_id is not None
            else event.event
        )
        sentry_sdk.capture_message(message, level="error")


async def log_event(
    level: LogLevel,
    event: str,
    account_id: str | None = None,
    extra: dict[str, object] | None = None,
) -> None:
    """Write one event to all three tiers (loguru, SQLite ``logs``, Sentry).

    ``extra`` is an open key/value bag. Keep payloads compact — large blobs
    bloat the ``logs`` table.
    """
    payload = LogEventInput(
        level=level,
        event=event,
        account_id=account_id,
        extra=extra or {},
    )

    _LOG_METHODS[payload.level](
        "{event} account_id={account_id} extra={extra}",
        event=payload.event,
        account_id=payload.account_id,
        extra=payload.extra,
    )

    await insert_log_row(payload)

    _send_to_sentry(payload)
