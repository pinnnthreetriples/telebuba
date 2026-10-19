"""Inbound SMS dispatcher: deduplication, ring-buffer inbox, cognitive pause, and ADB typing."""

from __future__ import annotations

import asyncio
import hashlib
import random
import time
import uuid
from collections import deque
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.config import settings
from core.emulator import adb, redroid, telegram_app
from core.emulator._errors import EmulatorError
from core.emulator.ui_dump import UiNode, fields
from schemas.telephony import (
    InboundSmsWebhook,
    ManualOtpInputResult,
    PhoneTelephonyStatus,
    SmsInboxList,
    SmsInboxMessage,
    SmsWebhookResponse,
)
from services.telephony._parser import detect_platform, extract_otp
from services.telephony._session import (
    find_active_session,
    get_session,
    normalize_phone,
    update_session_status,
)

if TYPE_CHECKING:
    from core.emulator.ui_dump import Screen

INBOX_MAX_MESSAGES = 50
DEDUP_TTL_SECONDS = 60.0
COGNITIVE_PAUSE_MIN_SEC = 5.0
COGNITIVE_PAUSE_MAX_SEC = 7.5
_DEFAULT_CODE_BOUNDS: tuple[int, int, int, int] = (240, 560, 840, 720)
_VERIFICATION_SCREENS: frozenset[Screen] = frozenset({"code", "password"})

_INBOX: deque[SmsInboxMessage] = deque(maxlen=INBOX_MAX_MESSAGES)
_SEEN_WEBHOOKS: dict[str, float] = {}
_ACTIVE_TASKS: set[asyncio.Task[None]] = set()
_RNG = random.Random()  # nosec B311  # noqa: S311


def _webhook_hash(phone: str, text: str) -> str:
    normalized = normalize_phone(phone) or phone.strip()
    payload_bytes = f"{normalized}:{text}".encode()
    return hashlib.sha256(payload_bytes).hexdigest()


def _is_duplicate_and_record(phone: str, text: str, now_mono: float | None = None) -> bool:
    moment = now_mono if now_mono is not None else time.monotonic()
    expired = [
        digest
        for digest, seen_at in _SEEN_WEBHOOKS.items()
        if moment - seen_at >= DEDUP_TTL_SECONDS
    ]
    for digest in expired:
        del _SEEN_WEBHOOKS[digest]
    digest = _webhook_hash(phone, text)
    if digest in _SEEN_WEBHOOKS:
        return True
    _SEEN_WEBHOOKS[digest] = moment
    return False


async def _resolve_serial(phone_id: str) -> str:
    """Resolve container name to ADB serial when emulator is enabled; fallback to ``phone_id``."""
    if settings.emulator.enabled:
        with suppress(EmulatorError, OSError):
            instance = await redroid.find_instance(phone_id)
            if instance is not None and instance.running:
                return redroid.adb_serial(instance.index)
    return phone_id


def _pick_focus_target(nodes: list[UiNode]) -> UiNode:
    editable = fields(nodes)
    if editable:
        return editable[0]
    if nodes:
        return nodes[0]
    return UiNode(
        text="",
        description="otp_input",
        class_name="android.widget.EditText",
        password=False,
        bounds=_DEFAULT_CODE_BOUNDS,
    )


async def _ensure_verification_focus(serial: str) -> None:
    """Read container screen and tap the verification input area if focus is lost."""
    try:
        screen, nodes = await telegram_app.read_screen(serial)
    except EmulatorError:
        return
    if screen not in _VERIFICATION_SCREENS:
        target = _pick_focus_target(nodes)
        with suppress(EmulatorError):
            await adb.human_tap(serial, target)


async def _type_otp_into_container(phone_id: str, code: str, *, pause: bool = True) -> None:
    """Run cognitive reading pause (5.0-7.5s), check screen focus, and type OTP via ADB."""
    if pause:
        delay = _RNG.uniform(COGNITIVE_PAUSE_MIN_SEC, COGNITIVE_PAUSE_MAX_SEC)
        await asyncio.sleep(delay)
    update_session_status(phone_id, "typing", last_code=code)
    serial = await _resolve_serial(phone_id)
    await _ensure_verification_focus(serial)
    await adb.human_type(serial, code)
    update_session_status(phone_id, "completed", last_code=code)


async def handle_inbound_sms(
    payload: InboundSmsWebhook,
    *,
    wait_for_typing: bool = False,
) -> SmsWebhookResponse:
    """Process an inbound SMS webhook, deduplicate, extract OTP, and dispatch typing."""
    if _is_duplicate_and_record(payload.phone, payload.text):
        return SmsWebhookResponse(status="ok", matched=False, code=None, duplicate=True)

    normalized_phone = normalize_phone(payload.phone) or payload.phone
    code = extract_otp(payload.text, payload.sender)
    platform = detect_platform(payload.text, payload.sender)
    session = find_active_session(normalized_phone)
    matched = session is not None and code is not None

    inbox_entry = SmsInboxMessage(
        id=uuid.uuid4().hex[:12],
        phone=normalized_phone,
        phone_id=session.phone_id if session is not None else None,
        sender=payload.sender,
        text=payload.text,
        code=code,
        platform=platform,
        matched=matched,
        timestamp=payload.timestamp,
    )
    _INBOX.appendleft(inbox_entry)

    if matched and session is not None and code is not None:
        update_session_status(
            session.phone_id,
            "otp_received",
            last_code=code,
            last_sms_text=payload.text,
        )
        task = asyncio.create_task(_type_otp_into_container(session.phone_id, code, pause=True))
        _ACTIVE_TASKS.add(task)
        task.add_done_callback(_ACTIVE_TASKS.discard)
        if wait_for_typing:
            await task

    return SmsWebhookResponse(
        status="ok",
        matched=matched,
        code=code,
        duplicate=False,
    )


async def wait_pending_dispatches() -> None:
    """Await all currently running background OTP typing tasks."""
    if _ACTIVE_TASKS:
        await asyncio.gather(*list(_ACTIVE_TASKS))


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
        created_at=session.created_at,
        expires_at=session.expires_at,
        remaining_seconds=remaining,
    )


async def manual_input_otp(phone_id: str, code: str) -> ManualOtpInputResult:
    """Manually type an OTP code into ``phone_id`` from the operator UI without SMS wait."""
    update_session_status(phone_id, "typing", last_code=code)
    serial = await _resolve_serial(phone_id)
    await _ensure_verification_focus(serial)
    await adb.human_type(serial, code)
    update_session_status(phone_id, "completed", last_code=code)
    return ManualOtpInputResult(
        status="ok",
        phone_id=phone_id,
        code=code,
        line_status="completed",
    )


async def list_inbox(phone_id: str | None = None) -> SmsInboxList:
    """Return the ring buffer of up to 50 recent inbound SMS messages."""
    if phone_id is None:
        return SmsInboxList(messages=list(_INBOX))
    session = get_session(phone_id)
    target_phone = session.phone_number if session is not None else None
    filtered = [
        msg
        for msg in _INBOX
        if msg.phone_id in {phone_id, None}
        and (target_phone is None or msg.phone == target_phone or msg.phone_id == phone_id)
    ]
    return SmsInboxList(messages=filtered)


def clear_dispatcher_state() -> None:
    """Clear inbox ring buffer and deduplication cache (used by tests)."""
    _INBOX.clear()
    _SEEN_WEBHOOKS.clear()
