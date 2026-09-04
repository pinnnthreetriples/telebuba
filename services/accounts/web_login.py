"""Open a signed-in web.telegram.org window for one account.

Each account gets AT MOST ONE :class:`LocalProxyRelay`, created lazily on the
first click and reused across clicks: the operator's browser window points at
``127.0.0.1:<relay_port>`` for its whole lifetime, so the relay must outlive a
single request and lives for the backend process. :func:`shutdown_web_login_relays`
tears them all down on app shutdown.

The first open launches WebK through the relay with a document-start hook that
captures WebK's own QR ``auth.loginToken``; we accept that token with the account's
authorized pooled client, so WebK finishes its OWN login (2FA password typed in when
Telegram asks). Every repeat open reuses that already-signed-in profile and must NOT
drive login again — accepting a second token spawns another 'Active Sessions'
device — so it just relaunches the browser through the relay.

Refusals surface as bounded, credential-free domain errors; proxy credentials and
the 2FA password never appear in any message or log.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

from telethon.errors import (
    AuthTokenAlreadyAcceptedError,
    AuthTokenExpiredError,
    AuthTokenInvalidxError,
)

from core.db import fetch_account_proxy_settings, fetch_account_twofa_password
from core.telegram_client import accept_web_login_token
from core.web_login import (
    LocalProxyRelay,
    account_profile_dir,
    latest_login_token,
    launch_webk_with_hook,
    page_state,
    relaunch_account_web,
    token_bytes,
    type_2fa_password,
)
from core.web_login._cdp import CdpError
from core.web_login.browser import BrowserNotFoundError
from schemas.accounts import OpenWebResult

if TYPE_CHECKING:
    from pathlib import Path

    from core.web_login._cdp import CdpSession
    from schemas.proxy import ProxySettings

__all__ = [
    "NoProxyForWebLoginError",
    "OpenWebResult",
    "WebLoginLaunchError",
    "WebLoginServiceError",
    "open_account_web",
    "shutdown_web_login_relays",
]

# The QR token WebK exports rotates; an accept against a stale one raises one of
# these, and the drive loop just tries the next captured token.
_ROTATION_ERRORS = (
    AuthTokenExpiredError,
    AuthTokenAlreadyAcceptedError,
    AuthTokenInvalidxError,
)

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


# One relay per account, created lazily and reused across clicks. The relay
# registry is guarded by a single lock so its dict access stays consistent; the
# whole per-account open is serialized by a PER-ACCOUNT lock (below) so the
# profile check + drive/relaunch decision cannot race. The relay lives until app
# shutdown.
_relays: dict[str, LocalProxyRelay] = {}
_relays_lock = asyncio.Lock()

# One lock per account so two concurrent first-clicks for the SAME account run the
# profile check + drive/relaunch decision serially, while different accounts still
# open concurrently. Created under a global guard.
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
    """Open (or reopen) a signed-in web.telegram.org window for ``account_id``.

    Requires a proxy: the browser must reach Telegram through the account's own
    exit, never the host's. First open drives the QR login into a new profile; every
    repeat reuses the signed-in profile without driving again. The whole per-account
    body is serialized so concurrent first-clicks cannot each start a login.
    """
    proxy = await fetch_account_proxy_settings(account_id)
    if proxy is None:
        msg = "account has no proxy assigned"
        raise NoProxyForWebLoginError(msg)
    async with await _account_open_lock(account_id):
        relay_port = await _relay_port_for(account_id, proxy)
        profile = account_profile_dir(account_id)
        if _profile_is_seeded(profile):
            await _relaunch(relay_port, profile)
        else:
            await _first_open(account_id, relay_port, profile)
    return OpenWebResult(launched=True)


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


async def _first_open(account_id: str, relay_port: int, profile: Path) -> None:
    """Launch WebK with the capture hook, drive the QR login, then close the socket."""
    try:
        session, _proc = await launch_webk_with_hook(relay_port, profile_dir=profile)
    except (BrowserNotFoundError, CdpError, TimeoutError, OSError) as exc:
        raise WebLoginLaunchError(str(exc)) from exc
    try:
        await _drive_login(account_id, session)
    except (CdpError, OSError) as exc:
        raise WebLoginLaunchError(str(exc)) from exc
    finally:
        # Close our CDP socket but leave the browser running — it is the operator's.
        await session.aclose()


async def _drive_login(account_id: str, session: CdpSession) -> None:
    """Poll WebK until it logs in: accept rotating QR tokens, type 2FA once."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _DRIVE_TIMEOUT
    accepted: set[str] = set()
    password_deadline: float | None = None
    while loop.time() < deadline:
        state = await page_state(session)
        if state == "logged_in":
            return
        if state == "password":
            if password_deadline is None:
                password_deadline = loop.time() + _PASSWORD_GRACE
                await _type_stored_password(account_id, session)
            elif loop.time() >= password_deadline:
                return  # leave the (blank or filled) screen for the operator
        elif state == "qr":
            await _accept_fresh_token(account_id, session, accepted)
        await asyncio.sleep(_POLL_INTERVAL)


async def _type_stored_password(account_id: str, session: CdpSession) -> None:
    """Type the stored 2FA password once, if one is on file (never logged)."""
    password = await fetch_account_twofa_password(account_id)
    if password:
        await type_2fa_password(session, password)


async def _accept_fresh_token(
    account_id: str,
    session: CdpSession,
    accepted: set[str],
) -> None:
    """Accept the newest unseen captured token; ignore a rotated/stale one."""
    token = await latest_login_token(session)
    if token is None or token in accepted:
        return
    accepted.add(token)
    with suppress(*_ROTATION_ERRORS):
        await accept_web_login_token(account_id, token_bytes(token))


async def _relaunch(relay_port: int, profile: Path) -> None:
    try:
        await relaunch_account_web(relay_port, profile_dir=profile)
    except BrowserNotFoundError as exc:
        raise WebLoginLaunchError(str(exc)) from exc


async def shutdown_web_login_relays() -> None:
    """Close every open relay on shutdown; each open browser loses its proxy."""
    async with _relays_lock:
        relays = list(_relays.values())
        _relays.clear()
    for relay in relays:
        with suppress(Exception):
            await relay.aclose()
