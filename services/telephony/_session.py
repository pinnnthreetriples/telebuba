"""In-memory store for OTP waiting sessions, keyed by phone and by its normalized number.

One phone holds at most one session and one number belongs to at most one phone:
registering a phone drops the number it waited on before, and a number another phone
was waiting on moves to the new phone (that phone's wait ends). Ended sessions stay
visible for ``session_retention_seconds`` and are then dropped, so the store stays
the size of the fleet. State is per process, which the one-worker rule allows.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from schemas.telephony import OtpSession

if TYPE_CHECKING:
    from schemas.telephony import TelephonyLineStatus, TelephonyPlatform

_RU_NATIONAL_LENGTH = 11
_NATIONAL_LENGTH = 10
_INTERNATIONAL_PREFIX = "00"
_ACTIVE: frozenset[TelephonyLineStatus] = frozenset({"waiting_otp"})
_BUSY: frozenset[TelephonyLineStatus] = frozenset({"otp_received", "typing"})

_SESSIONS_BY_PHONE_ID: dict[str, OtpSession] = {}
_PHONE_TO_ID: dict[str, str] = {}


def normalize_phone(phone_number: str) -> str:
    """Normalize a phone number to E.164 (``+<digits>``); ``""`` when it has no digits.

    Strips spaces, hyphens and parentheses, turns a ``00`` international prefix into
    ``+``, an 11-digit Russian ``8XXXXXXXXXX`` into ``+7XXXXXXXXXX``, and prefixes a
    bare 10-digit national number with ``TELEPHONY__DEFAULT_COUNTRY_CODE``.
    """
    raw = phone_number.strip()
    digits = re.sub(r"[^0-9]", "", raw)
    if not digits:
        return ""
    if raw.startswith("+"):
        return f"+{digits}"
    if digits.startswith(_INTERNATIONAL_PREFIX):
        return f"+{digits.removeprefix(_INTERNATIONAL_PREFIX)}"
    if len(digits) == _RU_NATIONAL_LENGTH and digits.startswith("8"):
        return f"+7{digits[1:]}"
    if len(digits) == _NATIONAL_LENGTH:
        return f"+{settings.telephony.default_country_code}{digits}"
    return f"+{digits}"


def _now() -> datetime:
    return datetime.now(UTC)


def _prune(now: datetime) -> None:
    """Drop ended sessions older than the retention, so the store never grows."""
    keep = timedelta(seconds=settings.telephony.session_retention_seconds)
    for phone_id, session in list(_SESSIONS_BY_PHONE_ID.items()):
        _expire_if_needed(session, now)
        if session.status not in _BUSY and now - session.updated_at >= keep:
            _drop(phone_id)


def _drop(phone_id: str) -> None:
    session = _SESSIONS_BY_PHONE_ID.pop(phone_id, None)
    if session is not None and _PHONE_TO_ID.get(session.phone_number) == phone_id:
        del _PHONE_TO_ID[session.phone_number]


def start_waiting_session(
    phone_id: str,
    phone_number: str,
    platform: TelephonyPlatform = "telegram",
    ttl_seconds: int | None = None,
) -> OtpSession:
    """Register ``phone_id`` as waiting for an OTP SMS on ``phone_number``.

    Replaces the phone's previous session (and the number it pointed at); a number
    another phone was waiting on is taken over and that phone's wait is dropped.
    """
    normalized = normalize_phone(phone_number)
    if not normalized:
        msg = "invalid phone_number"
        raise ValueError(msg)
    now = _now()
    _prune(now)
    _drop(phone_id)
    previous_owner = _PHONE_TO_ID.get(normalized)
    if previous_owner is not None:
        _drop(previous_owner)
    ttl = ttl_seconds if ttl_seconds is not None else settings.telephony.session_ttl_seconds
    session = OtpSession(
        phone_id=phone_id,
        phone_number=normalized,
        platform=platform,
        status="waiting_otp",
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(seconds=ttl),
    )
    _SESSIONS_BY_PHONE_ID[phone_id] = session
    _PHONE_TO_ID[normalized] = phone_id
    return session


def _expire_if_needed(session: OtpSession, now: datetime | None = None) -> OtpSession:
    moment = now or _now()
    if session.status == "waiting_otp" and moment >= session.expires_at:
        session.status = "timeout"
        session.updated_at = moment
    return session


def find_active_session(phone_number: str) -> OtpSession | None:
    """The session still WAITING for an OTP on ``phone_number``; ``None`` otherwise.

    A session that already got its code (``otp_received``/``typing``) is not active:
    a second SMS for it is kept in the inbox and never typed over the first.
    """
    normalized = normalize_phone(phone_number)
    phone_id = _PHONE_TO_ID.get(normalized) if normalized else None
    session = _SESSIONS_BY_PHONE_ID.get(phone_id) if phone_id is not None else None
    if session is None:
        return None
    _expire_if_needed(session)
    return session if session.status in _ACTIVE else None


def get_session(phone_id: str) -> OtpSession | None:
    """Return the latest OTP session for ``phone_id``, updating timeout if expired."""
    session = _SESSIONS_BY_PHONE_ID.get(phone_id)
    if session is None:
        return None
    return _expire_if_needed(session)


def update_session_status(
    phone_id: str,
    status: TelephonyLineStatus,
    *,
    last_code: str | None = None,
    last_sms_text: str | None = None,
    error_code: str | None = None,
) -> OtpSession | None:
    """Update the status (and optionally last code / SMS text / error) of a phone's session."""
    session = _SESSIONS_BY_PHONE_ID.get(phone_id)
    if session is None:
        return None
    session.status = status
    session.updated_at = _now()
    session.error_code = error_code
    if last_code is not None:
        session.last_code = last_code
    if last_sms_text is not None:
        session.last_sms_text = last_sms_text
    return session


def clear_sessions() -> None:
    """Reset in-memory sessions (used by tests)."""
    _SESSIONS_BY_PHONE_ID.clear()
    _PHONE_TO_ID.clear()
