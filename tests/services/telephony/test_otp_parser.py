"""Tests for OTP regex extraction across Telegram, WhatsApp, Facebook, TikTok, and Flash Call."""

from __future__ import annotations

import pytest

from services.telephony._parser import (
    detect_platform,
    extract_flash_call_otp,
    extract_otp,
)

# 18+ real-world RU & EN templates across Telegram, WhatsApp, Facebook, TikTok, and Generic
_REAL_SMS_TEMPLATES: tuple[tuple[str, str, str, str], ...] = (
    # Telegram: EN and RU templates
    ("Telegram", "Telegram code: 48291", "48291", "telegram"),
    ("Telegram", "Telegram code 59102\nDo not give this code to anyone.", "59102", "telegram"),
    ("Telegram", "48291 is your Telegram code", "48291", "telegram"),
    ("Telegram", "839201 is your Telegram login code.", "839201", "telegram"),
    (
        "Telegram",
        "Код Telegram: 71829\nНе давайте код никому, даже если его требуют от имени Telegram!",
        "71829",
        "telegram",
    ),
    (
        "Telegram",
        "Код для входа в Telegram: 60412. Никому не сообщайте этот код.",
        "60412",
        "telegram",
    ),
    ("Telegram", "51923 — ваш код Telegram.", "51923", "telegram"),
    ("Telegram", "90418", "90418", "telegram"),
    # WhatsApp: EN and RU templates
    (
        "WhatsApp",
        (
            "<#> Your WhatsApp code: 123-456\n"
            "You can also tap on this link to verify your phone: v.whatsapp.com/123456\n"
            "Don't share this code with others\n4sgLq1p5sV6"
        ),
        "123456",
        "whatsapp",
    ),
    (
        "WhatsApp",
        "<#> Код WhatsApp: 849-201\nНикому не сообщайте этот код\n4sgLq1p5sV6",
        "849201",
        "whatsapp",
    ),
    (
        "WhatsApp",
        (
            "Your WhatsApp Business account is being registered on a new device\n"
            "Do not share this code with anyone\n"
            "Your WhatsApp Business code: 390-112"
        ),
        "390112",
        "whatsapp",
    ),
    ("WhatsApp", "WhatsApp code 771 882", "771882", "whatsapp"),
    ("+15550009999", "654-321 is your WhatsApp verification code.", "654321", "whatsapp"),
    # Facebook: EN and RU templates
    ("Facebook", "123456 is your Facebook code\nLaz+nxCarLW", "123456", "facebook"),
    ("FB", "FB-987654 is your Facebook confirmation code", "987654", "facebook"),
    ("Facebook", "Код подтверждения Facebook: 12345678", "12345678", "facebook"),
    ("Facebook", "Facebook: 654321 — ваш код для сброса пароля.", "654321", "facebook"),
    # TikTok: EN and RU templates
    (
        "TikTok",
        (
            "[TikTok] 654321 is your verification code, valid for 5 minutes. "
            "To keep your account safe, never forward this code."
        ),
        "654321",
        "tiktok",
    ),
    (
        "TikTok",
        "[TikTok] 819203 — ваш код подтверждения. Никому его не сообщайте.",
        "819203",
        "tiktok",
    ),
    ("TikTok", "TikTok: 445566 is your verification code", "445566", "tiktok"),
    # Generic fallback (EN & RU)
    ("INFO", "Ваш код подтверждения: 9988. Никому не сообщайте его.", "9988", "generic"),
    ("Verify", "Your verification passcode is 74185290 for login.", "74185290", "generic"),
)


@pytest.mark.parametrize(
    ("sender", "text", "expected_code", "expected_platform"),
    _REAL_SMS_TEMPLATES,
)
def test_extract_otp_and_platform_from_real_templates(
    sender: str,
    text: str,
    expected_code: str,
    expected_platform: str,
) -> None:
    assert extract_otp(text, sender) == expected_code
    assert detect_platform(text, sender) == expected_platform


@pytest.mark.parametrize(
    ("sender", "multiline_text", "expected_code"),
    [
        (
            "Telegram",
            (
                "Внимание!\nПопытка входа с нового устройства Android 11 (Samsung Galaxy S23).\n"
                "Код Telegram: 38492\nХэш сессии: a89123b99102c"
            ),
            "38492",
        ),
        (
            "WhatsApp",
            (
                "<#> Привет!\nКто-то запрашивает доступ к аккаунту 2026-10-19.\n"
                "Ваш код WhatsApp: 901-442\nНе пересылайте сообщение.\nb998877a"
            ),
            "901442",
        ),
        (
            "Facebook",
            "Security notice #99\nUse FB-772109 to verify your login.\nRef: x123456y",
            "772109",
        ),
        (
            "TikTok",
            (
                "[TikTok]\nYour login verification code is:\n"
                "109283\nExpires in 5 minutes (id: 99a88b77c)."
            ),
            "109283",
        ),
    ],
)
def test_extract_otp_handles_multiline_and_noisy_messages(
    sender: str,
    multiline_text: str,
    expected_code: str,
) -> None:
    assert extract_otp(multiline_text, sender) == expected_code


@pytest.mark.parametrize(
    ("sender", "garbage_text"),
    [
        ("Promo", "Скидка 50% на тариф до 2026 года! Подробности на сайте example.com/a12345b"),
        ("Bank", "Баланс карты *9912 пополнен. Спасибо за покупку!"),
        ("Operator", "Уважаемый абонент, пакет интернета продлён. Хэш: 4sgLq1p5sV6"),
        ("Unknown", "Hello world! No digits or verification tokens here."),
    ],
)
def test_extract_otp_ignores_garbage_and_non_otp_messages(
    sender: str,
    garbage_text: str,
) -> None:
    assert extract_otp(garbage_text, sender) is None


@pytest.mark.parametrize(
    ("caller_id", "digits", "expected"),
    [
        ("+7 (999) 123-45-67", 4, "4567"),
        ("+79991234567", 5, "34567"),
        ("8-800-555-35-35", 6, "553535"),
        ("12345", 4, None),
    ],
)
def test_extract_flash_call_otp_extracts_trailing_digits(
    caller_id: str,
    digits: int,
    expected: str | None,
) -> None:
    assert extract_flash_call_otp(caller_id, digits=digits) == expected
    assert extract_otp("", caller_id, flash_call_digits=digits) == expected
    assert extract_otp("flash_call", caller_id, flash_call_digits=digits) == expected


def test_extract_flash_call_otp_rejects_invalid_digit_length() -> None:
    with pytest.raises(ValueError, match="digits must be between 4 and 6"):
        extract_flash_call_otp("+79991234567", digits=3)
    with pytest.raises(ValueError, match="digits must be between 4 and 6"):
        extract_flash_call_otp("+79991234567", digits=7)
