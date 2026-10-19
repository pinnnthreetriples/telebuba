"""Inbound SMS dispatcher: replay window, ring-buffer inbox, reading pause, and ADB typing.

A matched SMS moves its phone's session to ``otp_received`` in the same synchronous
step that finds it, so a second SMS for that phone is never matched while the first
is pending. Typing holds the phone's lock, and a manual input takes the same lock
after cancelling a code still in its reading pause, so two codes never interleave in
one field. A code is typed only when the phone's Telegram app shows its code screen
and no start or warming session holds the phone; anything else ends the wait as
``failed`` with the refusal code, never as an exception left on a background task.

Sessions, the replay window, the inbox and the tasks live in this process, which the
one-uvicorn-worker rule allows. Logs carry the phone id and a stable code, never the
number, the SMS text or the code.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import time
import uuid
from collections import OrderedDict, deque
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.config import settings
from core.emulator import redroid, telegram_app
from core.emulator._errors import EmulatorError
from core.logging import log_event
from schemas.telephony import (
    InboundSmsWebhook,
    ManualOtpInputResult,
    PhoneTelephonyStatus,
    SmsInboxList,
    SmsInboxMessage,
    SmsWebhookResponse,
)
from services.accounts import (
    DeviceRefusedError,
    EmulatorBusyError,
    EmulatorRefusedError,
    require_account,
)
from services.accounts.mobile_emulator import phone_activity
from services.device_farm import account_of
from services.telephony._parser import detect_platform, extract_otp
from services.telephony._session import (
    find_active_session,
    get_session,
    normalize_phone,
    start_waiting_session,
    update_session_status,
)

if TYPE_CHECKING:
    from schemas.devices import DeviceRefusalCode
    from schemas.emulator import EmulatorRefusalCode
    from schemas.telephony import OtpSession

INBOX_MAX_MESSAGES = 50

_DISABLED: EmulatorRefusalCode = "mobile_emulator_disabled"
_NOT_RUNNING: EmulatorRefusalCode = "emulator_not_running"
_NOT_ON_CODE_SCREEN: EmulatorRefusalCode = "emulator_field_missing"
_COMMAND_FAILED: EmulatorRefusalCode = "emulator_command_failed"
_PHONE_UNKNOWN: DeviceRefusalCode = "device_phone_unknown"
# What holds the phone's screen; a manual live-screen viewer does not.
_SCREEN_OWNERS = frozenset({"starting", "warming"})

_INBOX: deque[SmsInboxMessage] = deque(maxlen=INBOX_MAX_MESSAGES)
# Insertion order is arrival order, so the expired entries are always at the front.
_SEEN_WEBHOOKS: OrderedDict[str, float] = OrderedDict()
_DISPATCHES: dict[str, asyncio.Task[None]] = {}
_LOCKS: dict[str, asyncio.Lock] = {}
_RNG = random.Random()  # nosec B311  # noqa: S311


class StaleWebhookError(ValueError):
    """The gateway's timestamp is outside the replay window (400)."""

    def __init__(self) -> None:
        super().__init__("webhook_timestamp_out_of_window")


def _lock(phone_id: str) -> asyncio.Lock:
    return _LOCKS.setdefault(phone_id, asyncio.Lock())


def _webhook_hash(payload: InboundSmsWebhook, normalized_phone: str) -> str:
    parts = (payload.kind, normalized_phone, payload.sender, payload.text, payload.caller_id or "")
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def _is_duplicate_and_record(digest: str, now_mono: float | None = None) -> bool:
    moment = now_mono if now_mono is not None else time.monotonic()
    ttl = settings.telephony.dedup_ttl_seconds
    while _SEEN_WEBHOOKS:
        oldest, seen_at = next(iter(_SEEN_WEBHOOKS.items()))
        if moment - seen_at < ttl:
            break
        del _SEEN_WEBHOOKS[oldest]
    if digest in _SEEN_WEBHOOKS:
        return True
    _SEEN_WEBHOOKS[digest] = moment
    return False


def _check_timestamp(payload: InboundSmsWebhook, now: datetime) -> None:
    if payload.timestamp is None:
        return
    skew = abs((now - payload.timestamp).total_seconds())
    if skew > settings.telephony.max_clock_skew_seconds:
        raise StaleWebhookError


async def _resolve_serial(phone_id: str) -> str:
    """The running container's adb serial; refused when the emulator or the pod is down."""
    if not settings.emulator.enabled:
        raise EmulatorError(_DISABLED)
    try:
        instance = await redroid.find_instance(phone_id)
    except OSError as exc:
        raise EmulatorError(_COMMAND_FAILED) from exc
    if instance is None or not instance.running or instance.paused:
        raise EmulatorError(_NOT_RUNNING)
    return redroid.adb_serial(instance.index)


async def _enter_code(phone_id: str, code: str) -> None:
    """Type ``code`` into the phone's Telegram code screen, or refuse with a stable code."""
    if phone_activity(account_of(phone_id)) in _SCREEN_OWNERS:
        raise EmulatorBusyError
    serial = await _resolve_serial(phone_id)
    screen, nodes = await telegram_app.read_screen(serial)
    if screen != "code":
        raise EmulatorError(_NOT_ON_CODE_SCREEN)
    await telegram_app.enter_code(serial, nodes, code)


def _is_current(session: OtpSession) -> bool:
    return get_session(session.phone_id) is session


async def _fail(session: OtpSession, code: str) -> None:
    if _is_current(session):
        update_session_status(session.phone_id, "failed", error_code=code)
    await log_event(
        "WARNING",
        "telephony_otp_typing_failed",
        extra={"phone_id": session.phone_id, "code": code},
    )


async def _dispatch(session: OtpSession, code: str) -> None:
    """Reading pause, then type ``code`` unless the session was replaced meanwhile."""
    telephony = settings.telephony
    await asyncio.sleep(
        _RNG.uniform(telephony.reading_pause_min_seconds, telephony.reading_pause_max_seconds),
    )
    async with _lock(session.phone_id):
        if not _is_current(session) or session.status != "otp_received":
            return
        update_session_status(session.phone_id, "typing", last_code=code)
        try:
            await _enter_code(session.phone_id, code)
        except (EmulatorError, EmulatorRefusedError) as exc:
            await _fail(session, exc.code)
            return
        except Exception:  # noqa: BLE001 - a background task has nobody to raise to
            await _fail(session, _COMMAND_FAILED)
            return
        if _is_current(session):
            update_session_status(session.phone_id, "completed", last_code=code)


def _forget_dispatch(phone_id: str, task: asyncio.Task[None]) -> None:
    if _DISPATCHES.get(phone_id) is task:
        del _DISPATCHES[phone_id]


def _start_dispatch(session: OtpSession, code: str) -> None:
    task = asyncio.create_task(_dispatch(session, code))
    _DISPATCHES[session.phone_id] = task
    task.add_done_callback(lambda done: _forget_dispatch(session.phone_id, done))


async def handle_inbound_sms(payload: InboundSmsWebhook) -> SmsWebhookResponse:
    """Process an inbound SMS or Flash Call: replay checks, OTP extraction, dispatch."""
    now = datetime.now(UTC)
    _check_timestamp(payload, now)
    normalized_phone = normalize_phone(payload.phone) or payload.phone.strip()
    if _is_duplicate_and_record(_webhook_hash(payload, normalized_phone)):
        return SmsWebhookResponse(status="ok", matched=False, code=None, duplicate=True)

    session = find_active_session(normalized_phone)
    if payload.kind == "flash_call":
        code = extract_otp(payload.text, payload.sender, caller_id=payload.caller_id)
        platform = session.platform if session is not None else "generic"
    else:
        code = extract_otp(payload.text, payload.sender)
        platform = detect_platform(payload.text, payload.sender)
    matched = session is not None and code is not None

    _INBOX.appendleft(
        SmsInboxMessage(
            id=uuid.uuid4().hex[:12],
            phone=normalized_phone,
            phone_id=session.phone_id if matched and session is not None else None,
            sender=payload.sender,
            text=payload.text,
            code=code,
            platform=platform,
            matched=matched,
            timestamp=payload.timestamp or now,
        ),
    )

    if session is not None and code is not None:
        # Same synchronous step as the lookup: no second SMS can match in between.
        update_session_status(
            session.phone_id,
            "otp_received",
            last_code=code,
            last_sms_text=payload.text,
        )
        _start_dispatch(session, code)

    return SmsWebhookResponse(status="ok", matched=matched, code=code, duplicate=False)


async def start_phone_wait(phone_id: str) -> PhoneTelephonyStatus:
    """Make the phone wait for an OTP SMS on its account's number (``waiting_otp``)."""
    account_id = account_of(phone_id)
    account = await require_account(account_id)
    if not account.phone:
        raise DeviceRefusedError(_PHONE_UNKNOWN)
    if phone_activity(account_id) in _SCREEN_OWNERS:
        raise EmulatorBusyError
    start_waiting_session(phone_id, account.phone, platform="telegram")
    return await get_phone_status(phone_id)


async def wait_pending_dispatches() -> None:
    """Await all currently running background OTP typing tasks; never raises their errors."""
    if _DISPATCHES:
        await asyncio.gather(*list(_DISPATCHES.values()), return_exceptions=True)


async def get_phone_status(phone_id: str) -> PhoneTelephonyStatus:
    """Return the current telephony line status and remaining wait time for ``phone_id``."""
    session = get_session(phone_id)
    if session is None:
        return PhoneTelephonyStatus(phone_id=phone_id, status="idle", remaining_seconds=0)
    now = datetime.now(UTC)
    remaining = (
        max(0, int((session.expires_at - now).total_seconds()))
        if session.status == "waiting_otp"
        else 0
    )
    return PhoneTelephonyStatus(
        phone_id=session.phone_id,
        phone_number=session.phone_number,
        platform=session.platform,
        status=session.status,
        last_code=session.last_code,
        last_sms_text=session.last_sms_text,
        error_code=session.error_code,
        created_at=session.created_at,
        expires_at=session.expires_at,
        remaining_seconds=remaining,
    )


async def manual_input_otp(phone_id: str, code: str) -> ManualOtpInputResult:
    """Type ``code`` into ``phone_id`` now, superseding a code still in its reading pause.

    Refused (``EmulatorRefusedError``, 400 with the stable code) when the phone is not
    on its code screen, not running, or held by a start or warming session.
    """
    session = get_session(phone_id)
    pending = _DISPATCHES.get(phone_id)
    if pending is not None and session is not None and session.status == "otp_received":
        pending.cancel()
    async with _lock(phone_id):
        update_session_status(phone_id, "typing", last_code=code)
        try:
            await _enter_code(phone_id, code)
        except EmulatorError as exc:
            update_session_status(phone_id, "failed", error_code=exc.code)
            raise EmulatorRefusedError(exc.code) from exc
        except EmulatorRefusedError as exc:
            update_session_status(phone_id, "failed", error_code=exc.code)
            raise
        update_session_status(phone_id, "completed", last_code=code)
    return ManualOtpInputResult(status="ok", phone_id=phone_id, code=code, line_status="completed")


async def list_inbox(phone_id: str | None = None) -> SmsInboxList:
    """The ring buffer of recent SMS; for a phone, only SMS matched to it or to its number."""
    if phone_id is None:
        return SmsInboxList(messages=list(_INBOX))
    session = get_session(phone_id)
    number = session.phone_number if session is not None else None
    filtered = [
        msg
        for msg in _INBOX
        if msg.phone_id == phone_id
        or (number is not None and msg.phone_id is None and msg.phone == number)
    ]
    return SmsInboxList(messages=filtered)


def clear_dispatcher_state() -> None:
    """Clear inbox ring buffer, replay window, tasks and locks (used by tests)."""
    _INBOX.clear()
    _SEEN_WEBHOOKS.clear()
    for task in _DISPATCHES.values():
        task.cancel()
    _DISPATCHES.clear()
    _LOCKS.clear()
