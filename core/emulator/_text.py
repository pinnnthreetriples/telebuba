"""Text that crosses ``adb shell``: the device runs it through ``sh``.

``input text`` gets one shell-escaped word: every character ``sh`` treats specially
is backslash-escaped and a space becomes ``%s`` (``input text`` turns it back into a
space). That makes a literal ``%`` ambiguous, and ``input text`` cannot type anything
outside printable ASCII, so text holding either is refused before anything is typed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.emulator._errors import EmulatorError

if TYPE_CHECKING:
    from schemas.emulator import EmulatorRefusalCode

_EMULATOR_TEXT_UNSUPPORTED: EmulatorRefusalCode = "emulator_text_unsupported"

# Everything ``sh`` gives a meaning to outside quotes, the backslash itself first.
_SHELL_SPECIAL = frozenset("\\'\"`$;|&()<>*?[]{}~#!^=,")
_FIRST_PRINTABLE = 0x21
_LAST_PRINTABLE = 0x7E


def escape_input_text(text: str) -> str:
    """``text`` as one ``input text`` argument, or ``emulator_text_unsupported``."""
    if not text:
        raise EmulatorError(_EMULATOR_TEXT_UNSUPPORTED)
    escaped: list[str] = []
    for char in text:
        if char == " ":
            escaped.append("%s")
        elif char == "%" or not _FIRST_PRINTABLE <= ord(char) <= _LAST_PRINTABLE:
            raise EmulatorError(_EMULATOR_TEXT_UNSUPPORTED)
        elif char in _SHELL_SPECIAL:
            escaped.append("\\" + char)
        else:
            escaped.append(char)
    return "".join(escaped)


def shell_quote(value: str) -> str:
    """``value`` single-quoted for ``sh`` (a quote inside closes, escapes, reopens)."""
    return "'" + value.replace("'", "'\\''") + "'"
