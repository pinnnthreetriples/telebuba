"""Three-tier logging gateway.

Single entry point for all logging across the project. Per non-negotiable #4
nothing else imports ``loguru`` or ``sentry_sdk`` — features and services
exclusively call :func:`log_event`.

Tiers (per ``context/logging.md``):

1. **loguru** rotating ``debug.log`` — diagnostic noise (stacktraces, retries,
   timings). Always on. :class:`_StdlibToLoguru` feeds the **stdlib** root logger
   into the same file, so the full third-party exception text that the
   ``extra``-bounding rule sends to a module's ``logging.getLogger(__name__)`` is
   durable on disk instead of living only in the process's stderr stream.
2. **SQLite ``logs`` table** via ``core.db.insert_log_row`` — structured
   business events with normalised ``status`` (success/warning/error). Drives
   the future Logs page.
3. **Sentry** — only ``ERROR`` events, only when ``SENTRY_DSN`` is configured.
   Skipped otherwise; nothing is sent in dev.

``setup_logging()`` is idempotent: safe to call multiple times (only the first
call performs side effects). ``main.py`` calls it once at startup.
"""

from __future__ import annotations

import inspect
import logging
import re
import traceback
from typing import TYPE_CHECKING, Any

import sentry_sdk
from loguru import logger

from core.config import settings
from core.db import _now_iso, insert_log_row
from core.events import publish as publish_event
from schemas.logs import LogEntry, LogEventInput

if TYPE_CHECKING:
    from schemas.logs import LogLevel


class _State:
    initialized: bool = False
    sentry_active: bool = False


_state = _State()


# Third-party exception text (python-socks, Telethon) can carry the proxy URL or its
# credentials; the stdlib bridge persists that text to ``debug.log``. Two layers: any
# ``user:pass@`` authority is masked by shape, and every proxy password the process has
# handed to a client is masked by value (a password with odd characters can defeat the
# shape).
# Segment lengths are capped so a long colon-heavy line with no ``@`` stays linear.
_CREDENTIALS_IN_URL = re.compile(r"(?<![\w])[^\s/@:'\"<>]{1,256}:[^\s/@'\"<>]{1,256}@")
_MASK = "***@"
_MIN_SECRET_LEN = 6  # shorter values would mangle ordinary text
_MAX_SECRET_OWNERS = 256
# owner -> the values it registered; re-registering an owner (proxy rotation) replaces
# its old values, and the oldest owner is dropped past the cap.
_SECRETS: dict[str, tuple[str, ...]] = {}


def register_log_secrets(owner: str, *values: str | None) -> None:
    """Mask ``owner``'s ``values`` (proxy username/password) in logs, replacing its earlier ones."""
    kept = tuple(v for v in values if v and len(v) >= _MIN_SECRET_LEN)
    _SECRETS.pop(owner, None)
    if kept:
        _SECRETS[owner] = kept
    while len(_SECRETS) > _MAX_SECRET_OWNERS:
        del _SECRETS[next(iter(_SECRETS))]


def register_log_secret(value: str | None) -> None:
    """Mask ``value`` (e.g. a proxy password) wherever it appears in a bridged stdlib log line."""
    if value:
        register_log_secrets(value, value)


def redact_secrets(text: str) -> str:
    """``text`` with ``user:pass@`` authorities and registered secrets masked."""
    text = _CREDENTIALS_IN_URL.sub(_MASK, text)
    for values in list(_SECRETS.values()):
        for secret in values:
            text = text.replace(secret, "***")
    return text


def _redact_deep(value: object) -> object:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {k: _redact_deep(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_deep(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_redact_deep(v) for v in value)
    return value


_EVENT_TEXT_KEYS = ("message", "logentry", "exception", "breadcrumbs", "extra")


def _sentry_before_send(event: Any, _hint: Any) -> Any:  # noqa: ANN401 - sentry's untyped dicts
    """Redact proxy credentials from message, log entry, exception text and breadcrumbs."""
    for key in _EVENT_TEXT_KEYS:
        if key in event:
            event[key] = _redact_deep(event[key])
    return event


def _sentry_before_breadcrumb(crumb: Any, _hint: Any) -> Any:  # noqa: ANN401
    return _redact_deep(crumb)


class _RedactConsole(logging.Filter):
    """Redacts a record's text before the stderr handler formats it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_secrets(record.getMessage())
        record.args = None
        if record.exc_info:
            record.exc_text = redact_secrets(
                "".join(traceback.format_exception(*record.exc_info)).rstrip("\n"),
            )
        return True


_console_filter = _RedactConsole()


class _StdlibToLoguru(logging.Handler):
    """Forward stdlib records into the loguru sink (the docs' ``InterceptHandler``).

    Not a second logging system: the one sink is the rotating file configured in
    :func:`setup_logging`, and this only gives stdlib records a way into it. The frame
    walk is the documented recipe — without it every bridged line would blame
    ``core.logging:emit`` instead of the module that logged it.

    No feedback loop is possible: ``log_event`` writes to loguru, loguru writes to a
    file, and nothing in that path emits a stdlib record.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:  # a custom stdlib level loguru does not know
            level = record.levelno
        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1
        message = record.getMessage()
        if record.exc_info:
            # Rendered here (not passed to loguru) so the exception text is redacted too.
            message += "\n" + "".join(traceback.format_exception(*record.exc_info))
        logger.opt(depth=depth).log(level, redact_secrets(message))


_bridge = _StdlibToLoguru()


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

    # Retention for the stdlib sink. Full third-party exception text goes to each
    # module's ``logging.getLogger(__name__)`` and never into ``extra`` (see
    # ``tests/test_logevent_extra_bounds``), but uvicorn's ``LOGGING_CONFIG`` has no
    # ``root`` key, so those records fell to ``logging.lastResort`` — stderr and
    # nothing durable. An operator could not recover why a proxy failed six hours ago
    # unless whatever launched uvicorn captured stderr. Bridging root into the sink
    # above puts the text back on disk. Deliberately file-only: loguru serves no
    # route, so this adds nothing to ``GET /logs`` or ``GET /events``.
    #
    # ``lastResort`` is re-added explicitly because it is exactly the handler that
    # fires today and it stops firing the moment root has any handler of its own —
    # reusing it (rather than a fresh ``StreamHandler``) is what makes the console
    # byte-identical instead of merely similar. Root's level stays at its default
    # WARNING, so the two handlers see the same records stderr saw before. uvicorn's
    # own loggers set ``propagate=False`` and keep their own handler, so their format
    # is untouched and they are not echoed here.
    root = logging.getLogger()
    if logging.lastResort is not None:  # typed optional — ``None`` only if disabled.
        logging.lastResort.addFilter(_console_filter)
        root.addHandler(logging.lastResort)
    root.addHandler(_bridge)

    if settings.logging.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.logging.sentry_dsn,
            traces_sample_rate=0.0,
            send_default_pii=False,
            # ``include_local_variables`` defaults to True, and the default logging
            # integration turns any ERROR record carrying ``exc_info`` into an event
            # with every frame's ``f_locals`` rendered by ``repr()``.
            # ``core/db_maintenance.py`` documents that exact mechanism and accepts it
            # "no secrets" — an assumption the cloud-password (2FA) feature
            # invalidated: a failed ``edit_2fa`` leaves the plaintext password, the
            # recovery address and the mailed code sitting in Telethon's own frames as
            # bare string locals, which no field-level ``repr=False`` can reach. This
            # is the only switch that covers third-party frames.
            include_local_variables=False,
            before_send=_sentry_before_send,
            before_breadcrumb=_sentry_before_breadcrumb,
        )
        _state.sentry_active = True
    else:
        _state.sentry_active = False

    _state.initialized = True


def reset_logging_for_tests() -> None:
    """Drop all loguru sinks and reset module state. For tests only."""
    root = logging.getLogger()
    # Paired with ``setup_logging``: the fixtures reset-then-setup per test, so leaving
    # these attached would stack a duplicate pair on every one of them.
    if logging.lastResort is not None:
        logging.lastResort.removeFilter(_console_filter)
        root.removeHandler(logging.lastResort)
    root.removeHandler(_bridge)
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

    Best-effort: a failure in any tier (e.g. SQLite write error) is logged
    to loguru but never raised to the caller, so business operations cannot
    be broken by a logging fault.
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

    try:
        row = await insert_log_row(payload)
    except Exception as exc:  # noqa: BLE001 — logging must never break callers.
        logger.warning("log_persist_failed event={event} error={error}", event=event, error=exc)
    else:
        # Fourth, best-effort tier: fan the persisted row out to live SSE
        # subscribers. Never let a bus fault break the caller (same contract).
        try:
            publish_event(row)
        except Exception as exc:  # noqa: BLE001 — live bus must never break logging.
            logger.warning(
                "event_publish_failed event={event} error={error}",
                event=event,
                error=exc,
            )

    try:
        _send_to_sentry(payload)
    except Exception as exc:  # noqa: BLE001 — Sentry SDK can hiccup; swallow.
        logger.warning("sentry_send_failed event={event} error={error}", event=event, error=exc)


def signal_event(event: str, extra: dict[str, object] | None = None) -> None:
    """Fan a transient SSE nudge to live subscribers — deliberately NOT persisted.

    The publish-without-insert path (contrast :func:`log_event`, which inserts a
    ``logs`` row *then* publishes). Builds an in-memory :class:`LogEntry` (``id=0``)
    and fans it straight out to SSE subscribers so the SPA re-reads over HTTP.

    Because no row is written, high-frequency refresh nudges (e.g. onboarding
    channel-joins) refresh the board live without flooding the event log — which
    is the entire reason this exists.
    """
    entry = LogEntry(
        id=0,
        created_at=_now_iso(),
        level="INFO",
        status="success",
        account_id=None,
        event=event,
        extra=extra or {},
    )
    publish_event(entry)
