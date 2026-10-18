"""Text through ``adb shell`` and the Luhn check of the container's IMEI."""

from __future__ import annotations

import pytest

from core.emulator._errors import EmulatorError
from core.emulator._profile import is_valid_imei, luhn_check_digit
from core.emulator._text import escape_input_text


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


def test_the_imei_check_digit_is_luhns() -> None:
    # The textbook example: 49015420323751 + check digit 8.
    assert luhn_check_digit("49015420323751") == "8"
    assert is_valid_imei("490154203237518")
    assert not is_valid_imei("490154203237517")
