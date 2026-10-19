"""Regex parser for OTP codes from Telegram, WhatsApp, Facebook, TikTok, and Flash Calls."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemas.telephony import TelephonyPlatform

# Telegram patterns: Telegram code, Код Telegram, etc.
_TELEGRAM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)(?:telegram|телеграм)(?:[^\d\n]{0,25}?)"
        r"(?:код|code)?[:\s\-—]+(?<![A-Za-z\d])(\d{5,6})(?![A-Za-z\d])"
    ),
    re.compile(
        r"(?i)(?:код|code)(?:[^\d\n]{0,20}?)"
        r"(?:telegram|телеграм)[:\s\-—]+(?<![A-Za-z\d])(\d{5,6})(?![A-Za-z\d])"
    ),
    re.compile(
        r"(?i)(?<![A-Za-z\d])(\d{5,6})(?![A-Za-z\d])\s*"
        r"(?:is your telegram(?:\s+login)?\s+code|[—\-]?\s*ваш код(?:\s+для входа в)?\s+telegram)"
    ),
    re.compile(r"(?i)(?:telegram|код|code)[:\s\-]+(?<![A-Za-z\d])(\d{5,6})(?![A-Za-z\d])"),
)
_TELEGRAM_SENDER_DIGITS = re.compile(r"(?<![A-Za-z\d\+-])(\d{5,6})(?![A-Za-z\d])")

# WhatsApp patterns: Your WhatsApp code, Код WhatsApp, etc.
_WHATSAPP_PAIR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)(?:whatsapp|ватсап)(?:[^\d\n]{0,30}?)"
        r"(?<![A-Za-z\d])(\d{3})[-\s]?(\d{3})(?![A-Za-z\d])"
    ),
    re.compile(
        r"(?i)(?<![A-Za-z\d])(\d{3})[-\s]?(\d{3})(?![A-Za-z\d])"
        r"(?:[^\d\n]{0,25}?)(?:whatsapp|ватсап)"
    ),
    re.compile(r"(?<![A-Za-z\d\-])(\d{3})-(\d{3})(?![A-Za-z\d\-])"),
)

# Facebook patterns:
# - "FB-123456 is your Facebook confirmation code", "123456 is your Facebook code"
_FACEBOOK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(?:facebook|fb)[:\s\-]+(?<![A-Za-z\d])(\d{5,8})(?![A-Za-z\d])"),
    re.compile(r"(?i)(?<![A-Za-z\d])(\d{5,8})(?![A-Za-z\d])\s+is your (?:facebook|fb|instagram)\b"),
    re.compile(
        r"(?i)(?:facebook|fb|фейсбук)(?:[^\d\n]{0,30}?)"
        r"(?<![A-Za-z\d])(\d{5,8})(?![A-Za-z\d])"
    ),
    re.compile(
        r"(?i)(?<![A-Za-z\d])(\d{5,8})(?![A-Za-z\d])"
        r"(?:[^\d\n]{0,30}?)(?:facebook|fb|фейсбук)"
    ),
)
_FACEBOOK_SENDER_DIGITS = re.compile(r"(?<![A-Za-z\d\+-])(\d{6,8})(?![A-Za-z\d])")

# TikTok patterns: verification code, код подтверждения, etc.
_TIKTOK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\[?(?:tiktok|тикток)\]?[:\s\-]*(?<![A-Za-z\d])(\d{4,6})(?![A-Za-z\d])"),
    re.compile(r"(?i)(?:tiktok|тикток)(?:[^\d\n]{0,35}?)(?<![A-Za-z\d])(\d{4,6})(?![A-Za-z\d])"),
    re.compile(r"(?i)(?<![A-Za-z\d])(\d{6})(?![A-Za-z\d])\s+is your verification code\b"),
    re.compile(r"(?i)(?<![A-Za-z\d])(\d{4,6})(?![A-Za-z\d])(?:[^\d\n]{0,30}?)(?:tiktok|тикток)"),
)
_TIKTOK_SENDER_DIGITS = re.compile(r"(?<![A-Za-z\d\+-])(\d{6})(?![A-Za-z\d])")

# Generic fallback patterns near keywords: код, code, verification, passcode, otp
_GENERIC_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)(?:код|code|verification|passcode|пароль|otp|подтверждени[яе])"
        r"(?:[^\d\n]{0,30}?)(?<![A-Za-z\d\+-])(\d{4,8})(?![A-Za-z\d])"
    ),
    re.compile(
        r"(?i)(?<![A-Za-z\d\+-])(\d{4,8})(?![A-Za-z\d])"
        r"(?:[^\d\n]{0,30}?)(?:код|code|verification|passcode|пароль|otp|подтверждени[яе])"
    ),
)

_FLASH_CALL_MIN_DIGITS = 7
_FLASH_CALL_MIN_LEN = 4
_FLASH_CALL_MAX_LEN = 6


def detect_platform(text: str, sender: str = "") -> TelephonyPlatform:
    """Detect the target platform from sender name or message text."""
    combined = f"{sender} {text}".lower()
    if "whatsapp" in combined or "ватсап" in combined:
        return "whatsapp"
    if "tiktok" in combined or "тикток" in combined:
        return "tiktok"
    if re.search(r"\b(?:facebook|fb|фейсбук)\b", combined):
        return "facebook"
    if "telegram" in combined or "телеграм" in combined:
        return "telegram"
    return "generic"


def extract_flash_call_otp(caller_id: str, digits: int = 4) -> str | None:
    """Extract the last 4-6 digits of an incoming Flash Call phone number."""
    if not (_FLASH_CALL_MIN_LEN <= digits <= _FLASH_CALL_MAX_LEN):
        msg = "digits must be between 4 and 6"
        raise ValueError(msg)
    cleaned = re.sub(r"\D", "", caller_id)
    if len(cleaned) < _FLASH_CALL_MIN_DIGITS:
        return None
    return cleaned[-digits:]


def _extract_whatsapp(text: str, sender_low: str) -> str | None:
    is_wa = "whatsapp" in sender_low or "whatsapp" in text.lower() or "ватсап" in text.lower()
    for idx, pattern in enumerate(_WHATSAPP_PAIR_PATTERNS):
        if idx == len(_WHATSAPP_PAIR_PATTERNS) - 1 and not is_wa:
            continue
        match = pattern.search(text)
        if match is not None:
            return f"{match.group(1)}{match.group(2)}"
    return None


def _extract_facebook(text: str, sender_low: str) -> str | None:
    for pattern in _FACEBOOK_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            return match.group(1)
    if sender_low in {"facebook", "fb"}:
        match = _FACEBOOK_SENDER_DIGITS.search(text)
        if match is not None:
            return match.group(1)
    return None


def _extract_tiktok(text: str, sender_low: str) -> str | None:
    for pattern in _TIKTOK_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            return match.group(1)
    if "tiktok" in sender_low:
        match = _TIKTOK_SENDER_DIGITS.search(text)
        if match is not None:
            return match.group(1)
    return None


def _extract_telegram(text: str, sender_low: str) -> str | None:
    for pattern in _TELEGRAM_PATTERNS[:3]:
        match = pattern.search(text)
        if match is not None:
            return match.group(1)
    if "telegram" in sender_low or "telegram" in text.lower() or "телеграм" in text.lower():
        match = _TELEGRAM_PATTERNS[3].search(text)
        if match is not None:
            return match.group(1)
    if sender_low == "telegram":
        match = _TELEGRAM_SENDER_DIGITS.search(text)
        if match is not None:
            return match.group(1)
    return None


def extract_otp(
    text: str,
    sender: str = "",
    *,
    caller_id: str | None = None,
    flash_call_digits: int = 4,
) -> str | None:
    """Extract an OTP verification code from SMS text, sender, or Flash Call caller_id."""
    if caller_id is not None:
        return extract_flash_call_otp(caller_id, digits=flash_call_digits)

    stripped_text = text.strip()
    sender_clean = sender.strip()
    sender_low = sender_clean.lower()

    if not stripped_text or stripped_text.lower() in {"flash_call", "call", "ring"}:
        return extract_flash_call_otp(sender_clean, digits=flash_call_digits)

    for extractor in (_extract_whatsapp, _extract_facebook, _extract_tiktok, _extract_telegram):
        code = extractor(stripped_text, sender_low)
        if code is not None:
            return code

    hyphen_match = _WHATSAPP_PAIR_PATTERNS[-1].search(stripped_text)
    if hyphen_match is not None:
        return f"{hyphen_match.group(1)}{hyphen_match.group(2)}"

    for pattern in _GENERIC_PATTERNS:
        match = pattern.search(stripped_text)
        if match is not None:
            return match.group(1)

    return None
