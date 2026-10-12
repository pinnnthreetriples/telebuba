"""Text through ``adb shell`` and the container's identity from the mobile fingerprint."""

from __future__ import annotations

import pytest

from core.device_fingerprint_android import generate_android_fingerprint
from core.emulator._errors import EmulatorError
from core.emulator._profile import container_spec, generate_imei, is_valid_imei, luhn_check_digit
from core.emulator._text import escape_input_text, shell_quote


@pytest.mark.parametrize(
    ("text", "escaped"),
    [
        ("54321", "54321"),
        ("pass word", "pass%sword"),
        ("a;b|c&d", "a\\;b\\|c\\&d"),
        ("$(rm -rf)`x`", "\\$\\(rm%s-rf\\)\\`x\\`"),
        ("q'u\"o\\te", "q\\'u\\\"o\\\\te"),
        ("<>*?[]{}~#!", "\\<\\>\\*\\?\\[\\]\\{\\}\\~\\#\\!"),
    ],
)
def test_shell_specials_are_escaped_for_input_text(text: str, escaped: str) -> None:
    assert escape_input_text(text) == escaped


@pytest.mark.parametrize("text", ["100%", "пароль", "tab\there", "emoji🙂", ""])
def test_text_input_text_cannot_type_is_refused(text: str) -> None:
    with pytest.raises(EmulatorError) as refused:
        escape_input_text(text)

    assert refused.value.code == "emulator_text_unsupported"


def test_a_link_is_single_quoted_for_the_device_shell() -> None:
    assert shell_quote("tg://socks?server=h&port=1") == "'tg://socks?server=h&port=1'"
    assert shell_quote("it's") == "'it'\\''s'"


def test_the_imei_check_digit_is_luhns() -> None:
    # The textbook example: 49015420323751 + check digit 8.
    assert luhn_check_digit("49015420323751") == "8"
    assert is_valid_imei("490154203237518")
    assert not is_valid_imei("490154203237517")


def test_every_generated_imei_is_fifteen_digits_and_passes_luhn() -> None:
    for _ in range(200):
        imei = generate_imei()
        assert len(imei) == 15
        assert imei.isdigit()
        assert is_valid_imei(imei)


def test_the_container_is_the_phone_the_fingerprint_describes() -> None:
    fingerprint = generate_android_fingerprint("acc", None).model_copy(
        update={"device_model": "samsungSM-S918B", "system_version": "SDK 34"},
    )

    spec = container_spec(fingerprint)

    assert (spec.manufacturer, spec.model, spec.sdk) == ("samsung", "SM-S918B", 34)
    assert is_valid_imei(spec.imei)


@pytest.mark.parametrize(
    ("model", "sdk"),
    [("UnknownPhone 1", "SDK 34"), ("GooglePixel 8", "SDK 33"), ("GooglePixel 7", "Android 13")],
)
def test_a_fingerprint_no_container_can_present_is_refused(model: str, sdk: str) -> None:
    fingerprint = generate_android_fingerprint("acc", None).model_copy(
        update={"device_model": model, "system_version": sdk},
    )

    with pytest.raises(EmulatorError) as refused:
        container_spec(fingerprint)

    assert refused.value.code == "emulator_device_unknown"
