"""Open a signed-in web.telegram.org window for one account.

Each account gets AT MOST ONE :class:`LocalProxyRelay` and at most one open window,
both created lazily on the first click and both living for the backend process. The
window is held, not just launched: its browser-level CDP socket is what carries the
account's fingerprint, and Chrome strips every override the moment that socket drops.
:func:`shutdown_web_login` tears both registries down on app shutdown.

A click on an account whose window is still open raises that window — and, unless a
login has actually completed in that profile, drives the login again rather than
reporting a success that never happened. The first open launches WebK through the
relay with a document-start hook that captures WebK's own QR ``auth.loginToken``; we
accept that token with the account's authorized pooled client, so WebK finishes its
OWN login (2FA password typed in when Telegram asks). An open against an
already-signed-in profile must NOT drive login again — accepting a second token spawns
another 'Active Sessions' device — so it only relaunches or raises.

Refusals surface as bounded, credential-free domain errors carrying a FIXED snake_case
code the SPA translates; proxy credentials, third-party exception text and host paths
never reach the operator's toast. Driving an open page lives in ``_web_drive``.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import suppress
from typing import TYPE_CHECKING

from core.db import fetch_account_proxy_country, fetch_account_proxy_settings
from core.proxy_check import check_proxy_connectivity
from core.web_login import (
    LocalProxyRelay,
    account_profile_dir,
    fingerprint_for,
    focus_window,
    launch_account_web,
)
from core.web_login._cdp import CdpError
from core.web_login.browser import BrowserNotFoundError, BrowserStartError, WebWindow
from schemas.accounts import OpenWebResult
from services.accounts._web_drive import (
    drive_login,
    forget_signed_in,
    mark_signed_in,
    profile_is_seeded,
    still_signed_in,
)

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

# Overall bound on driving a first-open login. The poll cadence within it, the 2FA
# grace and the profile marker belong to ``_web_drive``.
_DRIVE_TIMEOUT = 90.0

# FIXED operator-facing refusals. ``str(exc)`` here would pipe arbitrary third-party
# text — Telethon's, binascii's, an OSError carrying an absolute host path — straight
# into a toast, so every refusal below picks one of these instead.
#
# CODES, not prose: the envelope's message is translated by the SPA through its
# ``shell.code.<code>`` tables (frontend/src/shared/i18n/{en,ru}.json) and an unknown
# string falls through verbatim — so English sentences here reach a Russian-first
# operator as English. Every code added must be added to BOTH tables.
_NO_PROXY_REFUSAL = "web_login_no_proxy"
_LAUNCH_REFUSAL = "web_login_browser_failed"
_DRIVE_REFUSAL = "web_login_drive_failed"
_RELAY_REFUSAL = "web_login_relay_failed"
_SHUTDOWN_REFUSAL = "web_login_shutting_down"
# ``uvicorn --reload`` (and ``--workers N``) forces a Windows SelectorEventLoop, which
# implements no subprocess transport at all: ``create_subprocess_exec`` raises
# NotImplementedError. Nothing in the code can fix that, so say what to change.
_RELOAD_LOOP_REFUSAL = "web_login_reload_loop"


class WebLoginServiceError(ValueError):
    """Base for web-login refusals the operator should see (maps to a 400)."""


class NoProxyForWebLoginError(WebLoginServiceError):
    """The account has no proxy assigned, so no relay can front the browser."""


class WebLoginLaunchError(WebLoginServiceError):
    """The relay or browser could not be started (never carries proxy creds)."""


# One relay and one window per account, created lazily and reused across clicks. Both
# registries are guarded by their own lock so dict access stays consistent; the whole
# per-account open is serialized by a PER-ACCOUNT lock (below) so the reuse check and
# the launch decision cannot race. The relay is stored WITH the proxy it fronts: an
# account reassigned to a different proxy must not keep exiting through the old one
# (and a deleted proxy would 502 every page load), so a mismatch rebinds.
_relays: dict[str, tuple[LocalProxyRelay, ProxySettings]] = {}
_relays_lock = asyncio.Lock()
_windows: dict[str, WebWindow] = {}
_windows_lock = asyncio.Lock()

# One lock per account so two concurrent first-clicks for the SAME account run the
# reuse check + launch decision serially, while different accounts still open
# concurrently. Created under a global guard.
_open_locks: dict[str, asyncio.Lock] = {}
_open_locks_guard = asyncio.Lock()

# Set as shutdown begins. ``shutdown_web_login`` takes the two registry locks but not
# the per-account one, so the read in ``open_account_web`` is only a fast path: what
# prevents a late registration is re-reading this flag inside each registration's own
# critical section (``_relay_port_for``, ``_spawn``) and ending the relay or window
# instead. An Event, not a module bool: reassigning a module global inside a function
# is banned by the lint config.
_closing = asyncio.Event()


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
    the window is launched wearing the account's fingerprint. Either way a profile that
    has never signed in gets the QR login driven into it, and the result says whether
    the account is actually signed in — an opened window is not a completed login.
    """
    proxy = await fetch_account_proxy_settings(account_id)
    if proxy is None:
        raise NoProxyForWebLoginError(_NO_PROXY_REFUSAL)
    profile = account_profile_dir(account_id)
    async with await _account_open_lock(account_id):
        # BEFORE the reuse check, or it is unreachable whenever a window is up: an
        # account reassigned to another proxy would keep exiting through the OLD one
        # for the life of the process, reported as a success, and 502 every page load
        # once that proxy row is deleted — with no way to recover by clicking.
        if await _relay_fronts_another_proxy(account_id, proxy):
            await _kill_registered_window(account_id)
        window = await _raise_open_window(account_id)
        if window is not None:
            reused = await _reuse_raised_window(account_id, window, profile)
            if reused is not None:
                return OpenWebResult(launched=True, signed_in=reused)
        if _closing.is_set():
            raise WebLoginLaunchError(_SHUTDOWN_REFUSAL)
        relay_port = await _relay_port_for(account_id, proxy)
        signed_in = await _launch(account_id, relay_port, profile, proxy)
    return OpenWebResult(launched=True, signed_in=signed_in)


async def _reuse_raised_window(
    account_id: str,
    window: WebWindow,
    profile: Path,
) -> bool | None:
    """What a raised window may claim; ``None`` means it was dropped — relaunch.

    Raising a window whose login never completed and calling that a success strands
    the operator: every later click focuses the same QR screen and returns "launched"
    without driving login again. Driving again is safe precisely because no login
    completed — it cannot spawn a second 'Active Sessions' device — but the stored 2FA
    password is NOT retyped (see :func:`services.accounts._web_drive.drive_login`).

    The marker cuts the other way too: it is a one-way on-disk latch nothing deletes,
    so a web session that dies later — revoked from Active Sessions, logged out inside
    the window, profile data cleared — would report signed-in on every click for good.
    """
    if not profile_is_seeded(profile):
        return await _drive_and_mark(account_id, window, profile, type_password=False)
    if await still_signed_in(window):
        return True
    # The stored session is gone. This window was launched WITHOUT the QR capture hook
    # (a seeded profile must not accept a second token) so it cannot be driven; forget
    # the marker and end it, and the relaunch opens one that can.
    forget_signed_in(profile)
    await _drop_window(account_id, window)
    return None


async def _raise_open_window(account_id: str) -> WebWindow | None:
    """Bring the account's still-open window to front; drop it when it is gone.

    The registry lock is held only to read and to pop. ``focus_window`` is a CDP
    command bounded at 30 s, and awaiting it under the process-wide lock would let one
    hung Chrome block EVERY other account's open — exactly what the per-account lock
    exists to prevent — and block shutdown with it.
    """
    async with _windows_lock:
        window = _windows.get(account_id)
    if window is None:
        return None
    if window.alive:
        try:
            await focus_window(window)
        except (CdpError, OSError):
            # The window died between the check and the call — fall through and
            # relaunch rather than reporting a raise that never happened.
            pass
        else:
            return window
    await _drop_window(account_id, window)
    return None


async def _drop_window(account_id: str, window: WebWindow) -> None:
    """Deregister this window and END it — never merely detach.

    ``aclose`` strips the fingerprint but leaves Chrome running, and ``focus_window``
    fails on a wedged renderer whose process is very much alive. The orphan then still
    holds the profile (so the next launch fails as a hand-off) and still points
    ``--proxy-server`` at a relay port this same open may rebind — after which it
    egresses through ANOTHER account's proxy.
    """
    async with _windows_lock:
        # Only drop the entry we actually examined; a concurrent open for this account
        # may already have registered a newer one.
        if _windows.get(account_id) is window:
            del _windows[account_id]
    with suppress(Exception):
        await window.kill()


async def _kill_registered_window(account_id: str) -> None:
    """End whatever window this account has registered, if any."""
    async with _windows_lock:
        window = _windows.get(account_id)
    if window is not None:
        await _drop_window(account_id, window)


async def _relay_fronts_another_proxy(account_id: str, proxy: ProxySettings) -> bool:
    """True when the cached relay is bound to a proxy the account no longer has."""
    async with _relays_lock:
        existing = _relays.get(account_id)
    return existing is not None and existing[1] != proxy


async def _relay_port_for(account_id: str, proxy: ProxySettings) -> int:
    """Get-or-create the account's relay for its CURRENT proxy; return its port."""
    async with _relays_lock:
        existing = _relays.get(account_id)
        if existing is not None:
            relay, fronted = existing
            if relay.port is not None and fronted == proxy:
                return relay.port
            # Half-started (no port), or bound to a proxy this account no longer has.
            # A relay fronting the wrong proxy sends the window out through someone
            # else's exit for the life of the process, and 502s every page load once
            # that proxy row is deleted. Drop the entry here, close it below.
            del _relays[account_id]
    if existing is not None:
        # OUTSIDE the registry lock: ``aclose`` cancels and awaits every in-flight
        # tunnel task, and holding a process-wide lock across that stalls every other
        # account's open — the same mistake ``_raise_open_window`` documents.
        with suppress(Exception):
            await existing[0].aclose()
    fresh = LocalProxyRelay(proxy)
    try:
        port = await fresh.start()
    except Exception as exc:
        raise WebLoginLaunchError(_RELAY_REFUSAL) from exc
    # ``_closing`` re-read inside the write's own critical section, never before it: a
    # shutdown landing since the fast-path check has cleared this registry already, and
    # refilling it leaves a socket listening past process exit.
    async with _relays_lock:
        if not _closing.is_set():
            _relays[account_id] = (fresh, proxy)
            return port
    with suppress(Exception):
        await fresh.aclose()
    raise WebLoginLaunchError(_SHUTDOWN_REFUSAL)


def _loop_cannot_spawn() -> bool:
    """True on the Windows SelectorEventLoop, whose subprocess transport is a stub.

    Only that loop earns the "restart without --reload" wording; a NotImplementedError
    from anywhere else would otherwise send the operator chasing a setting that is
    already right.
    """
    return sys.platform == "win32" and isinstance(
        asyncio.get_running_loop(), asyncio.SelectorEventLoop
    )


async def _fingerprint_country(account_id: str, proxy: ProxySettings) -> str | None:
    """The exit country the window's timezone and locale are aligned to.

    The stored one when a check has already resolved it, otherwise one measured now
    with the same probe the proxy pool uses. Without this second step the COMMON case
    — a freshly added proxy nobody has checked — falls through to the no-country
    default, and comparing a claimed timezone against the exit IP's geolocation is the
    most routinely computed geo check there is. A probe that cannot answer is not
    fatal: the default then claims no place at all rather than the wrong one.
    """
    stored = await fetch_account_proxy_country(account_id)
    if stored:
        return stored
    with suppress(Exception):
        return (await check_proxy_connectivity(proxy)).country_code
    return None


async def _spawn(
    account_id: str,
    relay_port: int,
    profile: Path,
    proxy: ProxySettings,
    *,
    capture: bool,
) -> WebWindow:
    """Launch a window wearing the account's fingerprint and register it."""
    try:
        # Inside the try: resolving the identity reads the INSTALLED browser (its build
        # and its brand), so "no browser" surfaces here as the same fixed refusal the
        # launch itself raises rather than as a bare 500.
        fingerprint = fingerprint_for(account_id, await _fingerprint_country(account_id, proxy))
        window = await launch_account_web(
            relay_port,
            profile_dir=profile,
            fingerprint=fingerprint,
            capture_tokens=capture,
        )
    except NotImplementedError as exc:
        refusal = _RELOAD_LOOP_REFUSAL if _loop_cannot_spawn() else _LAUNCH_REFUSAL
        raise WebLoginLaunchError(refusal) from exc
    # ``BrowserNotFoundError`` in the tuple, not a clause re-raising ``str(exc)``: both
    # its messages are English PROSE the SPA's code tables cannot translate, so they
    # reached a Russian-first operator verbatim. The fixed code's copy names both.
    except (BrowserNotFoundError, BrowserStartError, CdpError, TimeoutError, OSError) as exc:
        raise WebLoginLaunchError(_LAUNCH_REFUSAL) from exc
    # Register before driving: the window is the operator's from here, and a failed
    # login must not leak an undressed browser we no longer track. ``_closing`` re-read
    # as in ``_relay_port_for``: a late shutdown must kill this Chrome, not register it.
    async with _windows_lock:
        if not _closing.is_set():
            _windows[account_id] = window
            return window
    with suppress(Exception):
        await window.kill()
    raise WebLoginLaunchError(_SHUTDOWN_REFUSAL)


async def _launch(account_id: str, relay_port: int, profile: Path, proxy: ProxySettings) -> bool:
    """Launch the window, drive the login if needed; report whether it signed in.

    Keeps the invariant the raised-window path relies on: a REGISTERED window whose
    profile carries no marker was launched WITH the QR capture hook, so a later click
    can drive it. That is why a seeded profile whose session turns out to be dead is
    relaunched here rather than driven — the window already up has no hook, and a
    ``capture_tokens`` decision cannot be revisited after the launch.
    """
    seeded = profile_is_seeded(profile)
    window = await _spawn(account_id, relay_port, profile, proxy, capture=not seeded)
    if not seeded:
        return await _drive_and_mark(account_id, window, profile, type_password=True)
    if await still_signed_in(window):
        return True
    forget_signed_in(profile)
    await _drop_window(account_id, window)
    window = await _spawn(account_id, relay_port, profile, proxy, capture=True)
    return await _drive_and_mark(account_id, window, profile, type_password=True)


async def _drive_and_mark(
    account_id: str,
    window: WebWindow,
    profile: Path,
    *,
    type_password: bool,
) -> bool:
    """Drive the login under a hard deadline and mark the profile only on success.

    The deadline is a ``wait_for``, not a loop-entry check: each CDP command has its
    own 30 s timeout and typing an N-character password issues 2N+8 of them, so a
    wedged-but-connected renderer could otherwise hold the HTTP request for minutes.
    A timeout is "did not sign in", never an error — the window is up and the operator
    can finish by hand, and the profile stays unmarked so the next click drives again.
    """
    try:
        signed_in = await asyncio.wait_for(
            drive_login(account_id, window, type_password=type_password),
            _DRIVE_TIMEOUT,
        )
    except TimeoutError:
        return False
    except Exception as exc:
        # Blind on purpose: the callees raise anything. ``token_bytes`` raises
        # binascii.Error on a malformed capture, and ``accept_web_login_token`` raises
        # Telethon errors for a dead or deauthorized session — exactly the account an
        # operator wants to inspect. Uncaught, those are bare 500s with a traceback.
        raise WebLoginLaunchError(_DRIVE_REFUSAL) from exc
    if signed_in:
        mark_signed_in(profile)
    return signed_in


async def shutdown_web_login() -> None:
    """Kill every open window, then close every relay, on shutdown.

    Killed, not merely detached. Closing the socket already strips the fingerprint, and
    a window left running keeps ``--proxy-server`` pointed at a loopback port this same
    shutdown frees: after a restart another account's relay can bind that port, and the
    leftover window then egresses through the wrong account's proxy. The browsers are
    orphaned by this teardown anyway, so end them rather than leave them mis-pointed.

    The flag first, and both registrations re-read it under these same locks: without
    that, an ``open_account_web`` running beside this registers a fresh relay and a
    fresh window after both lists were read, past process exit.
    """
    _closing.set()
    async with _windows_lock:
        windows = list(_windows.values())
        _windows.clear()
    for window in windows:
        with suppress(Exception):
            await window.kill()
    async with _relays_lock:
        relays = [relay for relay, _proxy in _relays.values()]
        _relays.clear()
    for relay in relays:
        with suppress(Exception):
            await relay.aclose()
