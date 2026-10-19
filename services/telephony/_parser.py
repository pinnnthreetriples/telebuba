r"""Regex parser for OTP codes from Telegram, WhatsApp, Facebook, TikTok, and Flash Calls.

Text is NFKC-normalized first, so non-breaking and thin spaces become plain spaces and
full-width digits become ASCII. Codes are ``[0-9]`` only, never ``\d``: Python's
``\d`` also matches Arabic-Indic and other Unicode digits, which ``adb`` cannot type.
A code must stand on its own: no letter, digit or ``+`` right before it, and it may
not be a piece of a longer digit group such as ``123-45-67`` or ``999 123456``.
"""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from schemas.telephony import TelephonyPlatform

# Not glued to a word, a ``+`` (a phone number) or a preceding digit group.
_B = r"(?<![A-Za-z0-9+])(?<![0-9][-\s])"
# Not glued to a word, and not followed by another digit group.
_E = r"(?![A-Za-z0-9]|[-\s][0-9])"
# Up to N characters on the same line that are not digits.
_GAP = r"[^0-9\n]{{0,{n}}}?"

_TELEGRAM_KW = r"(?:telegram|телеграм)"
_WHATSAPP_KW = r"(?:whatsapp|ватсап)"
_FACEBOOK_KW = r"\b(?:facebook|fb|фейсбук)\b"
_TIKTOK_KW = r"(?:tiktok|тикток)"
_GENERIC_KW = r"(?:код|code|verification|passcode|пароль|otp|подтверждени[яе])"


def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


def _gap(n: int) -> str:
    return _GAP.format(n=n)


_TELEGRAM_PATTERNS: tuple[re.Pattern[str], ...] = (
    _compile(rf"{_TELEGRAM_KW}{_gap(30)}{_B}([0-9]{{5,6}}){_E}"),
    _compile(
        rf"{_B}([0-9]{{5,6}}){_E}\s*"
        rf"(?:is your telegram(?:\s+login)?\s+code|[—-]?\s*ваш код(?:\s+для входа в)?\s+telegram)"
    ),
)
# Only when the SMS names Telegram somewhere: a bare "code: 12345".
_TELEGRAM_CODE_WORD = _compile(rf"(?:код|code){_gap(15)}{_B}([0-9]{{5,6}}){_E}")
_TELEGRAM_SENDER_DIGITS = _compile(rf"{_B}([0-9]{{5,6}}){_E}")

_WHATSAPP_PAIR_PATTERNS: tuple[re.Pattern[str], ...] = (
    _compile(rf"{_WHATSAPP_KW}{_gap(30)}{_B}([0-9]{{3}})[-\s]?([0-9]{{3}}){_E}"),
    _compile(rf"{_B}([0-9]{{3}})[-\s]?([0-9]{{3}}){_E}{_gap(25)}{_WHATSAPP_KW}"),
)
# ``123-456`` with no keyword next to it, only in an SMS that is from or about WhatsApp.
_WHATSAPP_BARE_PAIR = _compile(rf"{_B}([0-9]{{3}})-([0-9]{{3}}){_E}")

_FACEBOOK_PATTERNS: tuple[re.Pattern[str], ...] = (
    _compile(rf"{_FACEBOOK_KW}{_gap(30)}{_B}([0-9]{{5,8}}){_E}"),
    _compile(rf"{_B}([0-9]{{5,8}}){_E}\s+is your (?:facebook|fb|instagram)\b"),
    _compile(rf"{_B}([0-9]{{5,8}}){_E}{_gap(30)}{_FACEBOOK_KW}"),
)
_FACEBOOK_SENDER_DIGITS = _compile(rf"{_B}([0-9]{{6,8}}){_E}")

_TIKTOK_PATTERNS: tuple[re.Pattern[str], ...] = (
    _compile(rf"\[?{_TIKTOK_KW}\]?{_gap(35)}{_B}([0-9]{{4,6}}){_E}"),
    _compile(rf"{_B}([0-9]{{6}}){_E}\s+is your verification code\b"),
    _compile(rf"{_B}([0-9]{{4,6}}){_E}{_gap(30)}{_TIKTOK_KW}"),
)
_TIKTOK_SENDER_DIGITS = _compile(rf"{_B}([0-9]{{6}}){_E}")

# A keyword right BEFORE the code, or an explicit "<code> is your code" phrase after
# it. A bare number followed somewhere later by "code" (an order number, a date) is
# not taken.
_GENERIC_PATTERNS: tuple[re.Pattern[str], ...] = (
    _compile(rf"{_GENERIC_KW}{_gap(15)}{_B}([0-9]{{4,8}}){_E}"),
    _compile(
        rf"{_B}([0-9]{{4,8}}){_E}\s*"
        r"(?:is your (?:verification |one-time |login )?(?:code|passcode|otp)\b"
        r"|[—-]?\s*(?:ваш|это) (?:код|пароль))"
    ),
)

_FLASH_CALL_MIN_DIGITS = 7
_FLASH_CALL_MIN_LEN = 4
_FLASH_CALL_MAX_LEN = 6


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip()


_PLATFORM_KEYWORDS: tuple[tuple[TelephonyPlatform, str], ...] = (
    ("whatsapp", _WHATSAPP_KW),
    ("tiktok", _TIKTOK_KW),
    ("facebook", _FACEBOOK_KW),
    ("telegram", _TELEGRAM_KW),
)


def _platform_named_in(value: str) -> TelephonyPlatform | None:
    lowered = _normalize(value).lower()
    for platform, keyword in _PLATFORM_KEYWORDS:
        if re.search(keyword, lowered):
            return platform
    return None


def detect_platform(text: str, sender: str = "") -> TelephonyPlatform:
    """Detect the platform from the sender name first, then from the message text."""
    return _platform_named_in(sender) or _platform_named_in(text) or "generic"


def extract_flash_call_otp(caller_id: str, digits: int = 4) -> str | None:
    """Extract the last 4-6 digits of an incoming Flash Call phone number."""
    if not (_FLASH_CALL_MIN_LEN <= digits <= _FLASH_CALL_MAX_LEN):
        msg = "digits must be between 4 and 6"
        raise ValueError(msg)
    cleaned = re.sub(r"[^0-9]", "", _normalize(caller_id))
    if len(cleaned) < _FLASH_CALL_MIN_DIGITS:
        return None
    return cleaned[-digits:]


def _first(patterns: tuple[re.Pattern[str], ...], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match is not None:
            return "".join(match.groups())
    return None


def _extract_whatsapp(text: str, sender_low: str) -> str | None:
    code = _first(_WHATSAPP_PAIR_PATTERNS, text)
    if code is None and re.search(_WHATSAPP_KW, f"{sender_low} {text.lower()}"):
        code = _first((_WHATSAPP_BARE_PAIR,), text)
    return code


def _extract_facebook(text: str, sender_low: str) -> str | None:
    code = _first(_FACEBOOK_PATTERNS, text)
    if code is None and sender_low in {"facebook", "fb"}:
        code = _first((_FACEBOOK_SENDER_DIGITS,), text)
    return code


def _extract_tiktok(text: str, sender_low: str) -> str | None:
    code = _first(_TIKTOK_PATTERNS, text)
    if code is None and "tiktok" in sender_low:
        code = _first((_TIKTOK_SENDER_DIGITS,), text)
    return code


def _extract_telegram(text: str, sender_low: str) -> str | None:
    code = _first(_TELEGRAM_PATTERNS, text)
    if code is None and re.search(_TELEGRAM_KW, f"{sender_low} {text.lower()}"):
        code = _first((_TELEGRAM_CODE_WORD,), text)
    if code is None and sender_low == "telegram":
        code = _first((_TELEGRAM_SENDER_DIGITS,), text)
    return code


_EXTRACTORS: dict[TelephonyPlatform, Callable[[str, str], str | None]] = {
    "whatsapp": _extract_whatsapp,
    "facebook": _extract_facebook,
    "tiktok": _extract_tiktok,
    "telegram": _extract_telegram,
}


def extract_otp(
    text: str,
    sender: str = "",
    *,
    caller_id: str | None = None,
    flash_call_digits: int = 4,
) -> str | None:
    """Extract an OTP code from SMS text and sender, or from a Flash Call ``caller_id``.

    A Flash Call is only ever read from an explicit ``caller_id``: an empty SMS from a
    number is not a call. The detected platform's extractor runs first, so an SMS that
    names two platforms is read by the one it is about.
    """
    if caller_id is not None:
        return extract_flash_call_otp(caller_id, digits=flash_call_digits)

    clean_text = _normalize(text)
    if not clean_text:
        return None
    sender_low = _normalize(sender).lower()

    platform = detect_platform(clean_text, sender_low)
    ordered = sorted(_EXTRACTORS.items(), key=lambda item: item[0] != platform)
    for _platform, extractor in ordered:
        code = extractor(clean_text, sender_low)
        if code is not None:
            return code
    return _first(_GENERIC_PATTERNS, clean_text)
