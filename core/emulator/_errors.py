"""The container layer's one failure type: a stable code, never third-party text."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemas.emulator import EmulatorRefusalCode


class EmulatorError(RuntimeError):
    """A container step failed; ``code`` is what the operator is shown.

    Console and adb output is never put in the message: an adb argv can carry the
    account's proxy credentials, and LDPlayer's text is unbounded.
    """

    def __init__(self, code: EmulatorRefusalCode) -> None:
        super().__init__(code)
        self.code: EmulatorRefusalCode = code
