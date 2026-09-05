"""Launch a per-account Chrome/Edge window that boots web.telegram.org/k/ signed in.

The browser is pointed at the account's :class:`LocalProxyRelay` (a credential-free
loopback proxy), given an isolated persistent per-account profile, and dressed in that
account's :class:`Fingerprint` by a :class:`TargetDriver` before the first navigation.

The CDP socket is BROWSER-level and stays open for as long as the window does. That is
not an optimisation: Chrome drops every emulation override and injected script the
moment the last DevTools client detaches, and each reload spawns a fresh MTProto worker
that has to be dressed while it is paused on start. Closing the socket early would hand
the operator's real machine straight to Telegram on the next reconnect.

On the first open we also inject a document-start hook that captures WebK's own QR
``auth.loginToken`` into ``window.__cap``; the caller accepts that token with the
account's authorized session so WebK completes its OWN login (no storage injection).

The primitives that drive an already-open page live in :mod:`core.web_login._page`.
"""

from __future__ import annotations

import asyncio
import base64
import os
import socket
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from core.config import settings
from core.secure_paths import make_private_dir
from core.web_login._cdp import CdpSession
from core.web_login._scripts import QR_CAPTURE_HOOK
from core.web_login._targets import TargetDriver
from core.web_login.fingerprint import note_browser_version

if TYPE_CHECKING:
    from core.web_login.fingerprint import Fingerprint

_WEBK_URL = "https://web.telegram.org/k/"
_LAUNCH_URL = "about:blank"
_PROFILE_SUBDIR = "web_profiles"
_CDP_READY_TIMEOUT = 20.0
# The endpoint is on loopback, where a connect to a port nobody listens on is refused
# in microseconds — so every tick of this interval is dead time in front of the
# operator's first window, and the only cost of shortening it is a few hundred extra
# refused loopback connects across the timeout above.
_CDP_POLL_INTERVAL = 0.05
# Chrome writes this into --user-data-dir the moment it starts listening: line 1 is the
# port, line 2 the browser target path. It is the only cheap proof that the endpoint we
# found belongs to the process WE spawned rather than another account's browser.
_ACTIVE_PORT_FILE = "DevToolsActivePort"
_ACTIVE_PORT_LINES = 2

# Picking a port and spawning are one critical section, process-wide. ``_free_port``
# binds :0 and closes again, so two accounts opening concurrently — which is the whole
# point of the per-account locks — can be handed the SAME port. The loser then either
# times out or, far worse, attaches to the other account's browser and re-dresses its
# page with the wrong fingerprint.
#
# The lock spans the pick and the spawn ONLY. Holding it across the 20 s wait for the
# DevTools endpoint would make one slow Chrome start delay every other account's open
# by up to that long. What the wait actually needs is that nobody else is handed the
# same port meanwhile, and ``_ports_in_flight`` gives exactly that: a port stays
# reserved until its launch has finished waiting, and a collision re-rolls.
_ports_in_flight: set[int] = set()
# ``_free_port`` asks the OS for an unused port, so a collision means a port picked by
# a launch that is still waiting; a handful of re-rolls is far more than enough.
_PORT_PICK_TRIES = 20


class BrowserNotFoundError(RuntimeError):
    """No Chrome or Edge executable was found in the usual Windows locations."""


class BrowserStartError(RuntimeError):
    """The browser exited before exposing a DevTools endpoint.

    Almost always a hand-off: a Chrome already running claimed this profile, took the
    command line and exited 0. Reported as itself rather than as a 20-second timeout,
    because the two need very different fixes.
    """


@dataclass(frozen=True)
class WebWindow:
    """One open browser window: the socket that dresses it, its page, its process.

    Everything here lives until the operator closes the window. ``aclose`` stops the
    driver and drops the socket — which also drops the fingerprint, so it is only for
    shutdown or a window that is already gone.
    """

    session: CdpSession
    driver: TargetDriver
    page: str
    process: asyncio.subprocess.Process

    @property
    def alive(self) -> bool:
        """True while the browser process runs and the DevTools socket is up."""
        return self.process.returncode is None and not self.session.closed

    async def aclose(self) -> None:
        await self.driver.aclose()
        await self.session.aclose()

    async def kill(self) -> None:
        """Close the socket AND end the browser process. Shutdown only.

        Leaving the window running is not neutral: it holds ``--proxy-server`` on a
        loopback port that the same shutdown frees, so after a restart a DIFFERENT
        account's relay can bind that port and this window silently egresses through
        the wrong account's proxy — a cross-account IP correlation nobody would see.
        Dropping the socket has already stripped its fingerprint anyway.

        ``aclose`` first, but in a ``try`` — a driver or pump task that ended with an
        exception outside the handful ``_route`` catches re-raises out of that await,
        and every caller wraps ``kill`` in ``suppress(Exception)``, so a bare second
        statement would leave the browser running and nobody the wiser.
        """
        try:
            await self.aclose()
        finally:
            await _end_process(self.process)


async def _end_process(process: asyncio.subprocess.Process) -> None:
    """Kill a browser we own and reap it, tolerating one that is already gone."""
    with suppress(OSError, ProcessLookupError):
        process.kill()
    with suppress(OSError, ProcessLookupError):
        await process.wait()


def _candidate_browsers() -> list[Path]:
    """Chrome first, then Edge, across Program Files / Program Files (x86) / LocalAppData."""
    roots = [
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    relatives = [
        (r"Google\Chrome\Application", "chrome.exe"),
        (r"Microsoft\Edge\Application", "msedge.exe"),
    ]
    return [Path(root) / subdir / exe for subdir, exe in relatives for root in roots if root]


def find_browser() -> Path:
    """Return the first installed Chrome (preferred) or Edge, or raise."""
    for candidate in _candidate_browsers():
        if candidate.exists():
            return candidate
    msg = "No Chrome or Edge executable found; install one to open a web session."
    raise BrowserNotFoundError(msg)


def build_launch_args(
    *,
    user_data_dir: Path,
    relay_port: int,
    url: str,
    debug_port: int,
    fingerprint: Fingerprint,
) -> list[str]:
    """The Chromium argv: isolated profile, loopback proxy, WebRTC guard, identity, app mode.

    The DevTools endpoint is always emitted: the fingerprint is applied over it and
    dies with it. Its allow-origin is scoped to that exact loopback origin rather than
    the lifetime-wide ``*``.

    ``--user-agent`` / ``--lang`` are the NETWORK-layer half of the identity.
    ``Emulation.setUserAgentOverride`` is page-scoped and never reaches a browser-level
    shared worker, so without these the very connection whose ``initConnection`` claims
    a Mac would carry the operator's real Chrome/Windows request headers.
    """
    return [
        f"--user-data-dir={user_data_dir}",
        f"--proxy-server=http://127.0.0.1:{relay_port}",
        # Keep WebRTC from leaking the real IP around the proxy. mDNS host-candidate
        # obfuscation stays ON: disabling it puts this desktop's LAN IP into the SDP of
        # every account's window — the same address in all of them.
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        f"--user-agent={fingerprint.user_agent}",
        f"--lang={fingerprint.locale}",
        f"--remote-debugging-port={debug_port}",
        # Recent Chrome refuses the CDP WebSocket without an allowed origin;
        # scope it to this endpoint instead of disabling the check with "*".
        f"--remote-allow-origins=http://127.0.0.1:{debug_port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--no-service-autorun",
        "--disable-sync",
        # Component updates, the optimization guide, Safe Browsing update fetches and
        # domain reliability all go through --proxy-server, and each one opens a fresh
        # upstream tunnel — TCP + greeting + auth + CONNECT, ~600 ms at a residential
        # RTT — competing with WebK's own bundle exactly while time-to-signed-in is
        # being decided. The umbrella flag covers the lot and touches no page load.
        # It also keeps Google-domain background traffic off the account's exit IP.
        "--disable-background-networking",
        f"--app={url}",
    ]


def account_profile_dir(account_id: str) -> Path:
    """Per-account persistent profile dir, sibling to the sessions dir. Persists between clicks.

    ABSOLUTE on purpose, and ``session_dir`` is relative in a default deployment. Given a
    relative ``--user-data-dir`` Chrome does not open an isolated profile at all: it hands
    its command line to whatever Chrome is already running and exits 0, so the account's
    window would appear in the OPERATOR's own browser, on the operator's own IP.
    """
    return (settings.telegram.session_dir.with_name(_PROFILE_SUBDIR) / account_id).resolve()


def token_bytes(b64url: str) -> bytes:
    """Decode a base64url login token (as the QR capture hook captured it) to raw bytes."""
    return base64.urlsafe_b64decode(b64url + "=" * (-len(b64url) % 4))


def _free_port() -> int:
    """Reserve a free loopback port the same way the relay does (bind :0, read, close)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _pick_port() -> int:
    """Reserve a debug port no launch that is still waiting has been handed.

    Reserving is synchronous on purpose: with no ``await`` between the check and the
    add, one event loop cannot interleave two callers here, so no lock is needed. The
    reservation is released by ``launch_account_web`` once its DevTools wait is over,
    by which point Chrome owns the port for real.
    """
    for _ in range(_PORT_PICK_TRIES):
        port = _free_port()
        if port not in _ports_in_flight:
            _ports_in_flight.add(port)
            return port
    msg = "No free DevTools port was available for the web-login browser."
    raise BrowserStartError(msg)


def _endpoint_is_foreign(profile_dir: Path, ws_url: str) -> bool:
    """True when this profile's ``DevToolsActivePort`` names a DIFFERENT browser target.

    Ownership proof, deliberately one-sided: a file that disagrees means the endpoint on
    our port belongs to somebody else (or is a stale listener), so the caller must keep
    waiting. A file that is missing proves nothing — Chrome may not have written it yet —
    and must never reject the endpoint, or a browser that does not write it at all could
    never be attached to.
    """
    try:
        lines = (profile_dir / _ACTIVE_PORT_FILE).read_text(encoding="utf-8").split("\n")
    except OSError:
        return False
    if len(lines) < _ACTIVE_PORT_LINES:
        return False
    return not ws_url.endswith(lines[1].strip())


async def _browser_ws(debug_port: int, process: asyncio.subprocess.Process, profile: Path) -> str:
    """Poll the DevTools endpoint for the BROWSER-level WebSocket URL.

    Browser level, not page level: a SHARED worker is a browser-scoped target, so a
    page-scoped socket would never be handed the one worker that matters most.

    A browser that has already exited is reported at once — waiting out the timeout
    would only mislabel a hand-off as a slow start.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _CDP_READY_TIMEOUT
    async with httpx.AsyncClient(trust_env=False) as client:
        while True:
            with_ws = await _try_browser_ws(client, debug_port)
            # Off the loop: a blocking ``read_text`` inside a 20 Hz poll is a stall
            # every other account's open pays for, however small each one is.
            if with_ws is not None and not await asyncio.to_thread(
                _endpoint_is_foreign, profile, with_ws
            ):
                return with_ws
            if process.returncode is not None:
                msg = (
                    f"The browser exited ({process.returncode}) without a DevTools "
                    "endpoint; a running Chrome most likely claimed this profile."
                )
                raise BrowserStartError(msg)
            if loop.time() >= deadline:
                msg = "Browser DevTools endpoint did not come up in time."
                raise TimeoutError(msg)
            await asyncio.sleep(_CDP_POLL_INTERVAL)


async def _try_browser_ws(client: httpx.AsyncClient, debug_port: int) -> str | None:
    """The browser WebSocket, recording the REAL browser build on the way past.

    ``/json/version`` reports the running binary as ``"Browser": "Chrome/<version>"``,
    and that is where the claimed version comes from: a hardcoded one drifts from the
    installed chrome.exe, and claiming a milestone the binary is not is one feature
    probe away from being caught. Only the first launch of a run predates an answer.
    """
    try:
        payload = (await client.get(f"http://127.0.0.1:{debug_port}/json/version")).json()
        url = payload.get("webSocketDebuggerUrl")
    except (httpx.HTTPError, ValueError, AttributeError):
        return None
    note_browser_version(payload.get("Browser"))
    return str(url) if url else None


async def launch_account_web(
    relay_port: int,
    *,
    profile_dir: Path,
    fingerprint: Fingerprint,
    capture_tokens: bool,
) -> WebWindow:
    """Launch WebK through the relay, dressed in ``fingerprint``, and return the window.

    Boots Chrome at ``about:blank`` in ``--app`` mode, attaches at browser level, lets
    the :class:`TargetDriver` dress the first page (and every later page/worker) while
    each is still paused, then navigates to ``/k/``. ``capture_tokens`` adds the QR
    login hook — first open only, since a repeat accept would spawn a second device.

    The caller owns the window and MUST keep it: closing the session undresses the
    browser. Every failure after the spawn kills the browser instead of orphaning it:
    a Chrome nobody holds a DevTools client for has already had its fingerprint
    stripped, still claims this account's ``--user-data-dir`` (so every later open for
    it fails as a hand-off) and is sitting on Telegram as the operator's real machine.
    """
    make_private_dir(profile_dir)
    browser = find_browser()
    # No lock around the spawn: ``_pick_port`` reserves synchronously, so on one event
    # loop nothing can interleave between its check and its add, and the reservation
    # already covers the window until Chrome binds the port. Serialising the spawn as
    # well cost every later account a full Chrome start — measured on this machine,
    # three simultaneous opens went from 0.7 s each to 2.0-2.4 s each for no guarantee.
    debug_port = _pick_port()
    args = build_launch_args(
        user_data_dir=profile_dir,
        relay_port=relay_port,
        debug_port=debug_port,
        url=_LAUNCH_URL,
        fingerprint=fingerprint,
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            str(browser),
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except BaseException:
        # The reservation is released by the wait below, which a spawn that never
        # returned does not reach: NotImplementedError on a Windows
        # SelectorEventLoop (``uvicorn --reload``, where EVERY click fails here),
        # FileNotFoundError, an OSError under handle pressure. Leaked, the port is
        # off the pool for the life of the process.
        _ports_in_flight.discard(debug_port)
        raise
    try:
        ws_url = await _browser_ws(debug_port, proc, profile_dir)
    except BaseException:
        await _end_process(proc)
        raise
    finally:
        # Chrome owns the port from here (or the browser is gone), so the reservation
        # that kept a concurrent launch off it has done its job.
        _ports_in_flight.discard(debug_port)
    return await _attach(proc, ws_url, fingerprint, capture_tokens=capture_tokens)


async def _attach(
    proc: asyncio.subprocess.Process,
    ws_url: str,
    fingerprint: Fingerprint,
    *,
    capture_tokens: bool,
) -> WebWindow:
    """Dress the spawned browser over CDP and navigate it; kill it if any step fails."""
    session: CdpSession | None = None
    driver: TargetDriver | None = None
    try:
        session = await CdpSession.connect(ws_url)
        driver = TargetDriver(
            session,
            fingerprint,
            page_scripts=(QR_CAPTURE_HOOK,) if capture_tokens else (),
        )
        page = await driver.first_page_session()
        driver.start()
        await session.send_command("Page.navigate", {"url": _WEBK_URL}, session_id=page)
    except BaseException:
        for closer in (driver, session):
            if closer is not None:
                with suppress(Exception):
                    await closer.aclose()
        await _end_process(proc)
        raise
    return WebWindow(session=session, driver=driver, page=page, process=proc)


async def focus_window(window: WebWindow) -> None:
    """Raise an already-open window instead of launching a second one."""
    await window.session.send_command("Page.bringToFront", session_id=window.page)
