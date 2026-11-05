"""Driving a WebK window that is already open, and the profile's signed-in marker.

Split out of :mod:`services.accounts.web_login` so that module can stay about the
proxy gate, the relay/window registries and the refusals the operator sees. Nothing
here starts or owns a process: every function takes a window somebody else launched.

The deadline and the mapping of a failure onto a fixed refusal stay with the caller —
these raise whatever their collaborators raise.
"""

from __future__ import annotations

import asyncio
import time
import weakref
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.logging import log_event
from core.telegram_client import WebLoginTransientError, accept_web_login_token
from core.web_login import (
    latest_login_token,
    page_state,
    release_capture,
    token_bytes,
)
from core.web_login._cdp import CdpError
from services.accounts._web_drive_password import PasswordSubmitter

if TYPE_CHECKING:
    from pathlib import Path

    from core.web_login.browser import WebWindow
    from schemas.accounts import WebLoginNotSignedInReason

__all__ = [
    "DriveTrace",
    "accepted_login_hash",
    "drive_login",
    "forget_signed_in",
    "mark_signed_in",
    "profile_is_seeded",
    "still_signed_in",
]

# Two transitions ("the QR is up", "we are in") are only ever noticed by this poll, so
# the interval is added almost twice to every login — a large share of a 4.5-17 s
# time-to-signed-in. One poll is one or two CDP round trips on loopback, so paying it
# more often costs nothing measurable; the wait it removes is pure ``sleep``.
_POLL_INTERVAL = 0.5
# How long the QR hook may capture nothing, on a screen that needs a token, before that is
# logged as a drift indicator (WebK changing how it asks for the token).
_NO_TOKEN_WARN_SECONDS = 15.0
# Bound on waiting for a freshly navigated WebK to stop reading "loading" before its
# state is trusted; a signed-in profile pays it once per open.
_SETTLE_TIMEOUT = 20.0
# The two states that prove a profile is NOT signed in, whatever the marker says.
_LOGIN_SCREENS = ("qr", "password")
# Written into a profile only once a login there reached the logged-in screen. Chrome
# ignores files it does not know, and the leading dot keeps it out of its way.
_SIGNED_IN_MARKER = ".telebuba-signed-in"
# After a transient accept failure the accept is held off for a growing time (never the
# loop: the password step and the state reads keep their own pace), up to this cap.
_ACCEPT_BACKOFF_BASE = 1.0
_ACCEPT_BACKOFF_MAX = 20.0
# The authorization hash Telegram returned for the QR accepted in each window. Kept with the
# window (not the drive) so a re-drive of the same window, after a hand-finished login, still
# knows which authorization is ours.
_accepted_hashes: weakref.WeakKeyDictionary[WebWindow, str] = weakref.WeakKeyDictionary()


def accepted_login_hash(window: WebWindow) -> str | None:
    """The hash of the authorization our accept created in ``window``, when one was seen."""
    return _accepted_hashes.get(window)


def profile_is_seeded(profile: Path) -> bool:
    """True only once a login in this profile actually reached the logged-in screen.

    Deliberately NOT "the directory has files in it": Chrome fills a fresh profile
    within a second of launching, long before anyone signs in. Keyed on files, a first
    open that timed out — or that the operator simply closed — would make every later
    click take the already-signed-in path and never drive login again, leaving a QR
    screen that no amount of clicking can get past.
    """
    return (profile / _SIGNED_IN_MARKER).exists()


def mark_signed_in(profile: Path) -> None:
    """Record that this profile completed a login. Best effort: the marker is a hint."""
    with suppress(OSError):
        (profile / _SIGNED_IN_MARKER).touch()


def forget_signed_in(profile: Path) -> None:
    """Retract the marker once the page proves the web session is gone."""
    with suppress(OSError):
        (profile / _SIGNED_IN_MARKER).unlink(missing_ok=True)


@dataclass
class DriveTrace:
    """What a drive last saw, still readable after the caller's deadline cancelled it.

    A timed-out drive returns nothing, yet "WebK never loaded" (usually the proxy) and
    "nobody took the QR" ask the operator for different things, so the loop records
    as it goes and the caller classifies afterwards. The counters are what an
    investigation reads from the log: none of them is a secret.

    ``started_at`` is the wall-clock moment the drive began (for the log).
    """

    state: str | None = None
    typed_password: bool = False
    has_stored_password: bool | None = None
    password_attempts: int = 0
    password_checking: bool = False
    password_rejected: bool = False
    tokens_seen: int = 0
    tokens_accepted: int = 0
    transient_retries: int = 0
    accept_hold_until: float = 0.0
    cdp_errors: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    _started_clock: float = field(default_factory=time.monotonic)

    @property
    def elapsed(self) -> float:
        """Seconds since the drive began."""
        return time.monotonic() - self._started_clock

    def failure_code(self) -> str:
        """The fine-grained outcome of a drive that ended without logging in (for the log)."""
        if self.state == "password":
            if self.typed_password:
                # "Rejected" only on WebK's own wrong-password state; a deadline that hit
                # mid-check (or a screen that merely went quiet) is not a verdict.
                if self.password_rejected:
                    return "web_login_2fa_rejected"
                return (
                    "web_login_2fa_check_timeout"
                    if self.password_checking
                    else "web_login_2fa_unconfirmed"
                )
            if self.has_stored_password is False:
                return "web_login_2fa_required"
            return "web_login_2fa_not_typed"
        if self.state == "qr":
            return "web_login_qr_timeout"
        return "web_login_page_not_loaded"

    def classify_failure(self) -> WebLoginNotSignedInReason:
        """The fixed code for a drive that ended without logging in."""
        code = self.failure_code()
        if code == "web_login_2fa_rejected":
            # Typed and still standing after the grace: the stored password is wrong.
            return "web_login_2fa_rejected"
        if code in (
            "web_login_2fa_required",
            "web_login_2fa_not_typed",
            "web_login_2fa_check_timeout",
            "web_login_2fa_unconfirmed",
        ):
            # Nothing typed (none stored, no field found), or Telegram was still checking
            # when time ran out: only a hand can finish it.
            return "web_login_2fa_required"
        if code == "web_login_qr_timeout":
            return "web_login_qr_timeout"
        return "web_login_page_not_loaded"


async def _settled_state(window: WebWindow) -> str:
    """Poll until WebK stops reading "loading"; the caller bounds the wait."""
    while True:
        state = await page_state(window)
        if state != "loading":
            return state
        await asyncio.sleep(_POLL_INTERVAL)


async def still_signed_in(window: WebWindow) -> bool:
    """One settled ``page_state`` reading: False once WebK is back on a login screen.

    The marker alone cannot answer this. It is a one-way on-disk latch nothing deletes,
    so a session that dies later — revoked from Active Sessions, logged out inside the
    window, profile data cleared — would report signed-in on every click for good, and
    across backend restarts: the same "screen no amount of clicking gets past" trap the
    marker exists to prevent, reached from the other side.

    Unreadable is NOT "signed out". A CDP hiccup or a page that never settles must not
    throw the marker away and send the account back through a login it does not need.
    """
    try:
        state = await asyncio.wait_for(_settled_state(window), _SETTLE_TIMEOUT)
    except Exception:  # noqa: BLE001 - any failure to read is "assume nothing changed".
        return True
    return state not in _LOGIN_SCREENS


async def drive_login(
    account_id: str,
    window: WebWindow,
    *,
    trace: DriveTrace,
) -> bool:
    """Poll WebK until it logs in: accept rotating QR tokens, type 2FA until it is sent.

    Returns whether the login actually completed. A ``False`` leaves the profile
    unmarked, so the next click drives the login again instead of assuming success.
    The caller owns the overall deadline; this loop only ends on a verdict, and
    ``trace`` is how the caller learns why it did not log in.

    The 2FA password reaches Telegram at most ONCE per window: a stale stored password
    (a normal ops state) retried is another ``auth.checkPassword`` failure against a live
    account, a walk into FLOOD_WAIT or a temporary lock with nothing on screen to say so.
    But "at most once" counts SUBMISSIONS, not attempts: while the field is not laid out,
    or does not take the text, nothing was sent and the typing is tried again. A re-drive
    of an already-open window types too, unless this window has already sent it (see
    :mod:`services.accounts._web_drive_password`).

    The accept is NOT gated on ``page_state`` returning "qr": WebK asks for
    ``auth.exportLoginToken`` before it paints the canvas, so the capture hook holds
    the token while the page still reads "loading" — waiting for the paint throws away
    a whole poll interval on every login. Repeat calls are already safe (the
    ``accepted`` set skips a token we sent, and a rotated one is simply refused).
    """
    accepted: set[str] = set()
    submitter = PasswordSubmitter(account_id, window, trace)
    warned = False
    while True:
        state = await _read_state(window, trace)
        trace.state = state
        if state == "logged_in":
            # The one moment at which the capture hook is provably finished with. It
            # holds live login tokens, and nothing inside the page can tell this moment
            # apart from the QR screen (WebK's shell markers are in the document, merely
            # hidden, the whole time), so the teardown is fired from here.
            await release_capture(window)
            return True
        await _accept_fresh_token(account_id, window, accepted, trace)
        if state == "password" and await submitter.step():
            # Leave the (blank or filled) screen for the operator. Not a success:
            # if they finish it by hand the next click simply drives again, which
            # is harmless, and if they abandon it nothing is wrongly remembered.
            return False
        if not warned and _hook_is_silent(state, trace):
            warned = True
            await log_event(
                "WARNING",
                "web_login_no_token_captured",
                account_id=account_id,
                extra={"state": state, "elapsed_s": round(trace.elapsed, 1)},
            )
        await asyncio.sleep(_POLL_INTERVAL)


def _hook_is_silent(state: str, trace: DriveTrace) -> bool:
    """True when the QR hook has captured nothing for too long on a screen that needs it.

    The usual cause is WebK having changed how it asks for the login token, which is
    otherwise invisible: the page just sits on its QR until the deadline.
    """
    return (
        trace.tokens_seen == 0 and state != "password" and trace.elapsed >= _NO_TOKEN_WARN_SECONDS
    )


async def _read_state(window: WebWindow, trace: DriveTrace) -> str:
    """One page reading; a CDP hiccup on a live window is "not loaded yet", not a failure."""
    try:
        return await page_state(window)
    except (CdpError, OSError):
        if not window.alive:
            raise
        trace.cdp_errors += 1
        return "loading"


async def _accept_fresh_token(
    account_id: str,
    window: WebWindow,
    accepted: set[str],
    trace: DriveTrace,
) -> None:
    """Accept the newest unseen captured token; ignore a rotated/stale one.

    A transient fault of the pooled client (connection, timeout, a short flood wait) is
    NOT a verdict on the token: it is forgotten so the next poll offers it again, inside
    the caller's deadline, instead of aborting a login that was about to work. The retry
    waits out a capped, growing hold (``trace.accept_hold_until``) that only this accept
    honours: sleeping here would stall the password step of the same poll.
    """
    if asyncio.get_running_loop().time() < trace.accept_hold_until:
        return
    try:
        token = await latest_login_token(window)
    except (CdpError, OSError):
        trace.cdp_errors += 1
        return
    if token is None or token in accepted:
        return
    accepted.add(token)
    trace.tokens_seen += 1
    try:
        # A rotated/stale token returns None; the loop just tries the next capture.
        new_hash = await accept_web_login_token(account_id, token_bytes(token))
        if new_hash is not None:
            trace.tokens_accepted += 1
            if new_hash:
                _accepted_hashes[window] = new_hash
    except WebLoginTransientError as exc:
        accepted.discard(token)
        trace.tokens_seen -= 1
        trace.transient_retries += 1
        delay = min(
            max(exc.retry_after, _ACCEPT_BACKOFF_BASE * 2 ** (trace.transient_retries - 1)),
            _ACCEPT_BACKOFF_MAX,
        )
        trace.accept_hold_until = asyncio.get_running_loop().time() + delay
