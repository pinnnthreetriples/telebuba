"""In-memory store for active OTP waiting sessions with E.164 phone normalization and TTL."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from schemas.telephony import OtpSession

if TYPE_CHECKING:
    from schemas.telephony import TelephonyLineStatus, TelephonyPlatform

_RU_NATIONAL_LENGTH = 11
_SESSIONS_BY_PHONE_ID: dict[str, OtpSession] = {}
_PHONE_TO_ID: dict[str, str] = {}


def normalize_phone(phone_number: str) -> str:
    """Normalize a phone number to canonical E.164 (+7..., +1..., etc.).

    Strips spaces, hyphens, parentheses, and converts 11-digit Russian ``8XXXXXXXXXX``
    numbers to ``+7XXXXXXXXXX``.
    """
    digits = re.sub(r"\D", "", phone_number)
    if not digits:
        return ""
    if len(digits) == _RU_NATIONAL_LENGTH and digits.startswith("8"):
        digits = f"7{digits[1:]}"
    return f"+{digits}"


def start_waiting_session(
    phone_id: str,
    phone_number: str,
    platform: TelephonyPlatform = "telegram",
    ttl_seconds: int = 180,
) -> OtpSession:
    """Register a phone container as waiting for an inbound OTP SMS with TTL."""
    normalized = normalize_phone(phone_number)
    if not normalized:
        msg = "invalid phone_number"
        raise ValueError(msg)
    now = datetime.now(UTC)
    session = OtpSession(
        phone_id=phone_id,
        phone_number=normalized,
        platform=platform,
        status="waiting_otp",
        created_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
    )
    _SESSIONS_BY_PHONE_ID[phone_id] = session
    _PHONE_TO_ID[normalized] = phone_id
    return session


def _expire_if_needed(session: OtpSession, now: datetime | None = None) -> OtpSession:
    moment = now or datetime.now(UTC)
    if session.status == "waiting_otp" and moment >= session.expires_at:
        session.status = "timeout"
    return session


def find_active_session(phone_number: str) -> OtpSession | None:
    """Find the active session waiting for an OTP on ``phone_number`` (normalized)."""
    normalized = normalize_phone(phone_number)
    if not normalized:
        return None
    phone_id = _PHONE_TO_ID.get(normalized)
    if phone_id is None:
        return None
    session = _SESSIONS_BY_PHONE_ID.get(phone_id)
    if session is None:
        return None
    _expire_if_needed(session)
    if session.status not in {"waiting_otp", "otp_received"}:
        return None
    return session


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
) -> OtpSession | None:
    """Update the status (and optionally last code / SMS text) of a phone's OTP session."""
    session = _SESSIONS_BY_PHONE_ID.get(phone_id)
    if session is None:
        return None
    session.status = status
    if last_code is not None:
        session.last_code = last_code
    if last_sms_text is not None:
        session.last_sms_text = last_sms_text
    return session


def clear_sessions() -> None:
    """Reset in-memory sessions (used by tests)."""
    _SESSIONS_BY_PHONE_ID.clear()
    _PHONE_TO_ID.clear()
