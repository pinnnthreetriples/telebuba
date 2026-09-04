"""Open a signed-in web.telegram.org window for one account.

Each account gets AT MOST ONE :class:`LocalProxyRelay`, created lazily on the
first click and reused across clicks: the operator's browser window points at
``127.0.0.1:<relay_port>`` for its whole lifetime, so the relay must outlive a
single request and lives for the backend process. :func:`shutdown_web_login_relays`
tears them all down on app shutdown.

The first open for an account MINTS a fresh authorization and seeds it into a new
persistent browser profile. Every repeat open reuses that already-signed-in
profile and must NOT mint again — a second mint spawns another 'Active Sessions'
device — so it just relaunches the browser through the relay with no re-seed.

``core.web_login.open_account_web`` (seed + launch) is imported here under the
alias ``_launch_seeded_web`` so it does not collide with this module's own
``open_account_web`` (the account-id orchestrator the API calls). Refusals surface
as bounded, credential-free domain errors; proxy credentials never appear in any
message or log.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

from core.db import fetch_account_proxy_settings
from core.telegram_client import (
    TwoFactorRequiredError,
    WebLoginError,
    mint_web_authorization,
)
from core.web_login import (
    LocalProxyRelay,
    account_profile_dir,
    relaunch_account_web,
)
from core.web_login import open_account_web as _launch_seeded_web
from core.web_login.browser import BrowserNotFoundError
from schemas.accounts import OpenWebResult

if TYPE_CHECKING:
    from pathlib import Path

    from schemas.proxy import ProxySettings

__all__ = [
    "NoProxyForWebLoginError",
    "OpenWebResult",
    "WebLoginLaunchError",
    "WebLoginServiceError",
    "WebLoginTwoFactorError",
    "open_account_web",
    "shutdown_web_login_relays",
]


class WebLoginServiceError(ValueError):
    """Base for web-login refusals the operator should see (maps to a 400)."""


class NoProxyForWebLoginError(WebLoginServiceError):
    """The account has no proxy assigned, so no relay can front the browser."""


class WebLoginTwoFactorError(WebLoginServiceError):
    """Minting needs the account's stored 2FA password and none is on file."""


class WebLoginLaunchError(WebLoginServiceError):
    """The relay or browser could not be started (never carries proxy creds)."""


# One relay per account, created lazily and reused across clicks. Guarded by a
# single lock so two near-simultaneous clicks for the same account cannot each
# bind a relay and leak one. The relay lives until app shutdown.
_relays: dict[str, LocalProxyRelay] = {}
_relays_lock = asyncio.Lock()


async def open_account_web(account_id: str) -> OpenWebResult:
    """Open (or reopen) a signed-in web.telegram.org window for ``account_id``.

    Requires a proxy: the browser must reach Telegram through the account's own
    exit, never the host's. First open mints + seeds a new profile; every repeat
    reuses the signed-in profile without minting again.
    """
    proxy = await fetch_account_proxy_settings(account_id)
    if proxy is None:
        msg = "account has no proxy assigned"
        raise NoProxyForWebLoginError(msg)
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
        relay = _relays.get(account_id)
        if relay is not None and relay.port is not None:
            return relay.port
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
    try:
        auth = await mint_web_authorization(account_id)
    except TwoFactorRequiredError as exc:
        msg = "2FA password required and not stored for this account"
        raise WebLoginTwoFactorError(msg) from exc
    except WebLoginError as exc:
        msg = "could not mint a web authorization for this account"
        raise WebLoginLaunchError(msg) from exc
    try:
        await _launch_seeded_web(auth, relay_port, profile_dir=profile)
    except BrowserNotFoundError as exc:
        raise WebLoginLaunchError(str(exc)) from exc


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
