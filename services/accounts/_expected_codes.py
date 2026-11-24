"""Login codes Telebuba itself is waiting for, per account (in memory, one uvicorn worker).

Telegram's service user sends a login code to the account's existing sessions. One that
arrives while Telebuba triggered nothing is somebody else trying to sign in: the inbox
journals it (``login_code_unrequested``). A flow that asks for a code stamps a window here
first; the stamps are monotonic, so a clock change cannot widen or close one.
"""

from __future__ import annotations

import time

__all__ = ["expect_login_code", "is_login_code_expected", "reset_for_tests"]

_EXPECTED_UNTIL: dict[str, float] = {}


def expect_login_code(account_id: str, seconds: float) -> None:
    """A code for ``account_id`` is expected for the next ``seconds`` (never shortens a window)."""
    until = time.monotonic() + seconds
    _EXPECTED_UNTIL[account_id] = max(_EXPECTED_UNTIL.get(account_id, 0.0), until)


def is_login_code_expected(account_id: str) -> bool:
    """Whether a window opened for ``account_id`` is still running."""
    return _EXPECTED_UNTIL.get(account_id, 0.0) > time.monotonic()


def reset_for_tests() -> None:
    """Forget every window between tests."""
    _EXPECTED_UNTIL.clear()
