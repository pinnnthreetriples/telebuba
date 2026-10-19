"""Telephony service: OTP regex parsing, waiting sessions, and biomechanical ADB dispatch."""

from __future__ import annotations

from services.telephony._dispatcher import (
    COGNITIVE_PAUSE_MAX_SEC,
    COGNITIVE_PAUSE_MIN_SEC,
    DEDUP_TTL_SECONDS,
    INBOX_MAX_MESSAGES,
    clear_dispatcher_state,
    get_phone_status,
    handle_inbound_sms,
    list_inbox,
    manual_input_otp,
    wait_pending_dispatches,
)
from services.telephony._parser import (
    detect_platform,
    extract_flash_call_otp,
    extract_otp,
)
from services.telephony._session import (
    clear_sessions,
    find_active_session,
    get_session,
    normalize_phone,
    start_waiting_session,
    update_session_status,
)

__all__ = [
    "COGNITIVE_PAUSE_MAX_SEC",
    "COGNITIVE_PAUSE_MIN_SEC",
    "DEDUP_TTL_SECONDS",
    "INBOX_MAX_MESSAGES",
    "clear_dispatcher_state",
    "clear_sessions",
    "detect_platform",
    "extract_flash_call_otp",
    "extract_otp",
    "find_active_session",
    "get_phone_status",
    "get_session",
    "handle_inbound_sms",
    "list_inbox",
    "manual_input_otp",
    "normalize_phone",
    "start_waiting_session",
    "update_session_status",
    "wait_pending_dispatches",
]
