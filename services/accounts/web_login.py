"""Open a signed-in web.telegram.org window for one account.

Each account gets AT MOST ONE :class:`LocalProxyRelay` and at most one open window,
both created lazily on the first click and both living for the backend process. The
window is held, not just launched: its browser-level CDP socket is what carries the
account's fingerprint, and Chrome strips every override the moment that socket drops.
:func:`shutdown_web_login` tears both registries down on app shutdown.

A click on an account whose window is still open just raises that window. The first
open launches WebK through the relay with a document-start hook that captures WebK's
own QR ``auth.loginToken``; we accept that token with the account's authorized pooled
client, so WebK finishes its OWN login (2FA password typed in when Telegram asks).
An open against an already-signed-in profile must NOT drive login again — accepting a
second token spawns another 'Active Sessions' device — so it only relaunches.

Refusals surface as bounded, credential-free domain errors; proxy credentials and
the 2FA password never appear in any message or log.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

from core.db import (
    fetch_account_proxy_country,
    fetch_account_proxy_settings,
    fetch_account_twofa_password,
)
from core.telegram_client import accept_web_login_token
from core.web_login import (
    LocalProxyRelay,
    account_profile_dir,
    fingerprint_for,
    focus_window,
    latest_login_token,
    launch_account_web,
    page_state,
    token_bytes,
    type_2fa_password,
)
from core.web_login._cdp import CdpError
from core.web_login.browser import BrowserNotFoundError, BrowserStartError, WebWindow
from schemas.accounts import OpenWebResult

if TYPE_CHECKING:
    from pathlib import Path

    from schemas.proxy import ProxySettings

__all__ = [
    "NoProxyForWebLoginError",
    "OpenWebResult",
    "WebLoginLaunchError",
    "WebLoginServiceError",
    "open_account_web",
    "shutdown_web_login",
]

# Overall bound on driving a first-open login, and the poll cadence within it.
_DRIVE_TIMEOUT = 90.0
_POLL_INTERVAL = 1.75
# After typing the 2FA password, wait this long for WebK to log in before leaving
# the screen for the operator — never re-submit (avoid a 2FA flood).
_PASSWORD_GRACE = 10.0


class WebLoginServiceError(ValueError):
    """Base for web-login refusals the operator should see (maps to a 400)."""


class NoProxyForWebLoginError(WebLoginServiceError):
    """The account has no proxy assigned, so no relay can front the browser."""


class WebLoginLaunchError(WebLoginServiceError):
    """The relay or browser could not be started (never carries proxy creds)."""


# One relay and one window per account, created lazily and reused across clicks. Both
# registries are guarded by their own lock so dict access stays consistent; the whole
# per-account open is serialized by a PER-ACCOUNT lock (below) so the reuse check and
# the launch decision cannot race.
_relays: dict[str, LocalProxyRelay] = {}
_relays_lock = asyncio.Lock()
_windows: dict[str, WebWindow] = {}
_windows_lock = asyncio.Lock()

# One lock per account so two concurrent first-clicks for the SAME account run the
# reuse check + launch decision serially, while different accounts still open
# concurrently. Created under a global guard.
_open_locks: dict[str, asyncio.Lock] = {}
_open_locks_guard = asyncio.Lock()


async def _account_open_lock(account_id: str) -> asyncio.Lock:
    """Get-or-create the per-account open lock, under the global guard."""
    async with _open_locks_guard:
        lock = _open_locks.get(account_id)
        if lock is None:
            lock = asyncio.Lock()
            _open_locks[account_id] = lock
        return lock


async def open_account_web(account_id: str) -> OpenWebResult:
    """Open (or raise) a signed-in web.telegram.org window for ``account_id``.

    Requires a proxy: the browser must reach Telegram through the account's own exit,
    never the host's. A still-open window is raised rather than duplicated; otherwise
    the window is launched wearing the account's fingerprint, and a profile that has
    never signed in also gets the QR login driven into it.
    """
    proxy = await fetch_account_proxy_settings(account_id)
    if proxy is None:
        msg = "account has no proxy assigned"
        raise NoProxyForWebLoginError(msg)
    async with await _account_open_lock(account_id):
        if await _raise_open_window(account_id):
            return OpenWebResult(launched=True)
        relay_port = await _relay_port_for(account_id, proxy)
        await _launch(account_id, relay_port, account_profile_dir(account_id))
    return OpenWebResult(launched=True)


async def _raise_open_window(account_id: str) -> bool:
    """Bring the account's still-open window to front; drop it when it is gone."""
    async with _windows_lock:
        window = _windows.get(account_id)
        if window is None:
            return False
        if window.alive:
            try:
                await focus_window(window)
            except (CdpError, OSError):
                # The window died between the check and the call — fall through and
                # relaunch rather than reporting a raise that never happened.
                pass
            else:
                return True
        _windows.pop(account_id, None)
    with suppress(Exception):
        await window.aclose()
    return False


async def _relay_port_for(account_id: str, proxy: ProxySettings) -> int:
    """Get-or-create the account's relay and return the loopback port it serves."""
    async with _relays_lock:
        existing = _relays.get(account_id)
        if existing is not None and existing.port is not None:
            return existing.port
        if existing is not None:
            # A stored relay that bound no port is half-started; close it before we
            # replace it so it can't be orphaned.
            await existing.aclose()
        relay = LocalProxyRelay(proxy)
        try:
            port = await relay.start()
        except Exception as exc:
            msg = "could not start the web-login proxy relay"
            raise WebLoginLaunchError(msg) from exc
        _relays[account_id] = relay
        return port


def _profile_is_seeded(profile: Path) -> bool:
    """A profile counts as signed-in once it exists and holds any files."""
    return profile.exists() and any(profile.iterdir())


async def _launch(account_id: str, relay_port: int, profile: Path) -> None:
    """Launch the window, register it, and drive the QR login on a fresh profile."""
    seeded = _profile_is_seeded(profile)
    fingerprint = fingerprint_for(account_id, await fetch_account_proxy_country(account_id))
    try:
        window = await launch_account_web(
            relay_port,
            profile_dir=profile,
            fingerprint=fingerprint,
            capture_tokens=not seeded,
        )
    except (BrowserNotFoundError, BrowserStartError, CdpError, TimeoutError, OSError) as exc:
        raise WebLoginLaunchError(str(exc)) from exc
    # Register before driving: the window is the operator's from this point, and a
    # failed login must not leak an undressed browser we no longer track.
    async with _windows_lock:
        _windows[account_id] = window
    if seeded:
        return
    try:
        await _drive_login(account_id, window)
    except (CdpError, OSError) as exc:
        raise WebLoginLaunchError(str(exc)) from exc


async def _drive_login(account_id: str, window: WebWindow) -> None:
    """Poll WebK until it logs in: accept rotating QR tokens, type 2FA once."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _DRIVE_TIMEOUT
    accepted: set[str] = set()
    password_deadline: float | None = None
    while loop.time() < deadline:
        state = await page_state(window)
        if state == "logged_in":
            return
        if state == "password":
            if password_deadline is None:
                password_deadline = loop.time() + _PASSWORD_GRACE
                await _type_stored_password(account_id, window)
            elif loop.time() >= password_deadline:
                return  # leave the (blank or filled) screen for the operator
        elif state == "qr":
            await _accept_fresh_token(account_id, window, accepted)
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


async def shutdown_web_login() -> None:
    """Close every open window and relay on shutdown.

    Closing a window's socket strips its fingerprint, so this runs only when the
    backend is going away and the browsers are about to be orphaned anyway.
    """
    async with _windows_lock:
        windows = list(_windows.values())
        _windows.clear()
    for window in windows:
        with suppress(Exception):
            await window.aclose()
    async with _relays_lock:
        relays = list(_relays.values())
        _relays.clear()
    for relay in relays:
        with suppress(Exception):
            await relay.aclose()
