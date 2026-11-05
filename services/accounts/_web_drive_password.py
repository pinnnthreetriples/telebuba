"""Typing the stored 2FA password into an open WebK window, and waiting out the verdict.

Split out of :mod:`services.accounts._web_drive` for its size. Two promises pull against
each other here, and the module exists to keep both:

* a REAL submission reaches Telegram at most once per window — a stale stored password
  retried is a walk into FLOOD_WAIT or a temporary lock on a live account;
* a submission that never happened (no field laid out yet, the field not taking the text)
  is retried until it does — the account's web login must not be lost to a slide animation.

"Submitted" is therefore a property of the WINDOW, not of one drive: a re-drive of a window
whose first drive timed out before anything was sent still types, and one whose password
was already sent never sends it again.
"""

from __future__ import annotations

import asyncio
import weakref
from typing import TYPE_CHECKING

from core.web_login import password_outcome, type_2fa_password
from core.web_login._cdp import CdpError

if TYPE_CHECKING:
    from core.web_login.browser import WebWindow
    from services.accounts._web_drive import DriveTrace

# After a submission, how long WebK may sit on the password screen with no sign of work
# before the screen is left to the operator. Generous because ``auth.checkPassword`` is
# SRP over the account's own (often residential) proxy, not one fast round trip.
_PASSWORD_GRACE = 15.0
# ...and how long it may keep reading "checking" (a busy button) before that too is given
# up. Bounded, and below the caller's whole-drive deadline so the verdict stays ours.
_PASSWORD_CHECK_MAX = 45.0
# With a password on file that never reached the field, how long to keep looking for the
# field before handing the screen over. No stored password hands it over after the grace.
_TYPE_RETRY_BUDGET = 45.0

_submitted: weakref.WeakSet[WebWindow] = weakref.WeakSet()


async def _authorising_password(account_id: str) -> str | None:
    """The stored password, a pending candidate settled against Telegram first."""
    from services.accounts.twofa import authorising_twofa_password  # noqa: PLC0415 - import cycle

    return await authorising_twofa_password(account_id)


def password_submitted(window: WebWindow) -> bool:
    """True once a 2FA password was actually sent to Telegram from this window."""
    return window in _submitted


class PasswordSubmitter:
    """One drive's view of the password screen: submit until sent, then wait for a verdict."""

    def __init__(self, account_id: str, window: WebWindow, trace: DriveTrace) -> None:
        self._account_id = account_id
        self._window = window
        self._trace = trace
        self._password: str | None = None
        self._fetched = False
        self._first_seen: float | None = None
        self._grace_ends: float | None = None
        self._check_ends: float | None = None
        trace.typed_password = password_submitted(window)

    async def step(self) -> bool:
        """One poll on the password screen. True means: leave it to the operator."""
        now = asyncio.get_running_loop().time()
        if self._first_seen is None:
            self._first_seen = now
        if password_submitted(self._window):
            return await self._verdict(now)
        await self._try_submit(now)
        if password_submitted(self._window):
            return False
        # Nothing sent. No password on file: hand over after the grace, as ever. One on
        # file: keep looking for the field, within a budget.
        budget = _TYPE_RETRY_BUDGET if self._password else _PASSWORD_GRACE
        return now - self._first_seen >= budget

    async def _try_submit(self, now: float) -> None:
        if not self._fetched:
            self._password = await _authorising_password(self._account_id)
            self._fetched = True
            self._trace.has_stored_password = bool(self._password)
        if not self._password:
            return
        self._trace.password_attempts += 1
        try:
            sent = await type_2fa_password(
                self._window, self._password, on_submit=self._mark_submitted
            )
        except (CdpError, OSError):
            # Dropped mid-typing: the field still holds a partial value, which the next
            # attempt clears first. A drop AFTER Enter was dispatched has already marked
            # the window (``_mark_submitted``), so it is never typed a second time.
            sent = False
        if sent or password_submitted(self._window):
            self._mark_submitted()
            self._grace_ends = now + _PASSWORD_GRACE
            self._check_ends = now + _PASSWORD_CHECK_MAX

    def _mark_submitted(self) -> None:
        """Called before Enter goes out: from here on the password may be at Telegram."""
        _submitted.add(self._window)
        self._trace.typed_password = True

    async def _verdict(self, now: float) -> bool:
        """After a submission: leave on a visible wrong-password state, or when time is up."""
        if self._grace_ends is None or self._check_ends is None:
            # Submitted by an earlier drive of this window: the clock starts here.
            self._grace_ends = now + _PASSWORD_GRACE
            self._check_ends = now + _PASSWORD_CHECK_MAX
        try:
            outcome = await password_outcome(self._window)
        except (CdpError, OSError):
            outcome = "idle"
        self._trace.password_checking = outcome == "checking"
        if outcome == "error":
            self._trace.password_rejected = True
            return True
        return now >= (self._check_ends if outcome == "checking" else self._grace_ends)
