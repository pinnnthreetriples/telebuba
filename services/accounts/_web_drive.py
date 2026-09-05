"""Driving a WebK window that is already open, and the profile's signed-in marker.

Split out of :mod:`services.accounts.web_login` so that module can stay about the
proxy gate, the relay/window registries and the refusals the operator sees. Nothing
here starts or owns a process: every function takes a window somebody else launched.

The deadline and the mapping of a failure onto a fixed refusal stay with the caller —
these raise whatever their collaborators raise.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

from core.db import fetch_account_twofa_password
from core.telegram_client import accept_web_login_token
from core.web_login import (
    latest_login_token,
    page_state,
    release_capture,
    token_bytes,
    type_2fa_password,
)

if TYPE_CHECKING:
    from pathlib import Path

    from core.web_login.browser import WebWindow

__all__ = [
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
# After typing the 2FA password, wait this long for WebK to log in before leaving
# the screen for the operator — never re-submit (avoid a 2FA flood).
_PASSWORD_GRACE = 10.0
# Bound on waiting for a freshly navigated WebK to stop reading "loading" before its
# state is trusted; a signed-in profile pays it once per open.
_SETTLE_TIMEOUT = 20.0
# The two states that prove a profile is NOT signed in, whatever the marker says.
_LOGIN_SCREENS = ("qr", "password")
# Written into a profile only once a login there reached the logged-in screen. Chrome
# ignores files it does not know, and the leading dot keeps it out of its way.
_SIGNED_IN_MARKER = ".telebuba-signed-in"


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


async def drive_login(account_id: str, window: WebWindow, *, type_password: bool) -> bool:
    """Poll WebK until it logs in: accept rotating QR tokens, type 2FA at most once.

    Returns whether the login actually completed. A ``False`` leaves the profile
    unmarked, so the next click drives the login again instead of assuming success.
    The caller owns the overall deadline; this loop only ends on a verdict.

    ``type_password`` is False when an ALREADY-OPEN window is driven again. Re-driving
    exists for the QR half, where every re-run is harmless; the password half is not.
    A stale stored password (a normal ops state) fails, leaves the window on the
    password screen, and the operator clicks again on the failure toast — each click
    another ``auth.checkPassword`` failure against a live account, walking it into a
    FLOOD_WAIT or a temporary lock with nothing on screen to say so. ``_PASSWORD_GRACE``
    only ever promised one submission per window; this keeps that promise.

    The accept is NOT gated on ``page_state`` returning "qr": WebK asks for
    ``auth.exportLoginToken`` before it paints the canvas, so the capture hook holds
    the token while the page still reads "loading" — waiting for the paint throws away
    a whole poll interval on every login. Repeat calls are already safe (the
    ``accepted`` set skips a token we sent, and a rotated one is simply refused).
    """
    loop = asyncio.get_running_loop()
    accepted: set[str] = set()
    password_deadline: float | None = None
    while True:
        state = await page_state(window)
        if state == "logged_in":
            # The one moment at which the capture hook is provably finished with. It
            # holds live login tokens, and nothing inside the page can tell this moment
            # apart from the QR screen (WebK's shell markers are in the document, merely
            # hidden, the whole time), so the teardown is fired from here.
            await release_capture(window)
            return True
        await _accept_fresh_token(account_id, window, accepted)
        if state == "password":
            if password_deadline is None:
                password_deadline = loop.time() + _PASSWORD_GRACE
                if type_password:
                    await _type_stored_password(account_id, window)
            elif loop.time() >= password_deadline:
                # Leave the (blank or filled) screen for the operator. Not a success:
                # if they finish it by hand the next click simply drives again, which
                # is harmless, and if they abandon it nothing is wrongly remembered.
                return False
        await asyncio.sleep(_POLL_INTERVAL)


async def _type_stored_password(account_id: str, window: WebWindow) -> None:
    """Type the stored 2FA password once, if one is on file (never logged)."""
    password = await fetch_account_twofa_password(account_id)
    if password:
        await type_2fa_password(window, password)


async def _accept_fresh_token(
    account_id: str,
    window: WebWindow,
    accepted: set[str],
) -> None:
    """Accept the newest unseen captured token; ignore a rotated/stale one."""
    token = await latest_login_token(window)
    if token is None or token in accepted:
        return
    accepted.add(token)
    # A rotated/stale token returns False; the loop just tries the next capture.
    await accept_web_login_token(account_id, token_bytes(token))
