"""Launch a per-account Chrome/Edge window that boots web.telegram.org/k/ signed in.

The browser is pointed at the account's :class:`LocalProxyRelay` (a credential-free
loopback proxy), given an isolated persistent per-account profile, and — before any
web.telegram.org document loads — seeded with the minted authorization's localStorage
via CDP. The operator's window is then left running; the relay lifecycle is the
caller's, not ours.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from core.config import settings
from core.secure_paths import make_private_dir
from core.web_login._cdp import CdpSession
from core.web_login.storage import build_webk_localstorage

if TYPE_CHECKING:
    from core.telegram_client._web_login import MintedWebAuth

_WEBK_URL = "https://web.telegram.org/k/"
_WEBK_ORIGIN = "https://web.telegram.org"
_LAUNCH_URL = "about:blank"
_PROFILE_SUBDIR = "web_profiles"
_CDP_READY_TIMEOUT = 20.0
_CDP_POLL_INTERVAL = 0.25


class BrowserNotFoundError(RuntimeError):
    """No Chrome or Edge executable was found in the usual Windows locations."""


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
    debug_port: int,
    url: str,
) -> list[str]:
    """The Chromium argv: isolated profile, loopback proxy, WebRTC guards, CDP, app mode."""
    return [
        f"--user-data-dir={user_data_dir}",
        f"--proxy-server=http://127.0.0.1:{relay_port}",
        # Keep WebRTC from leaking the real IP around the proxy.
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--disable-features=WebRtcHideLocalIpsWithMdns",
        f"--remote-debugging-port={debug_port}",
        # Recent Chrome refuses the CDP WebSocket without an allowed origin.
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--no-service-autorun",
        "--disable-sync",
        f"--app={url}",
    ]


def account_profile_dir(account_id: str) -> Path:
    """Per-account persistent profile dir, sibling to the sessions dir. Persists between clicks."""
    return settings.telegram.session_dir.with_name(_PROFILE_SUBDIR) / account_id


def _free_port() -> int:
    """Reserve a free loopback port the same way the relay does (bind :0, read, close)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


async def _discover_page_ws(debug_port: int) -> str:
    """Poll the DevTools HTTP endpoint until a page target's WebSocket URL appears."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _CDP_READY_TIMEOUT
    async with httpx.AsyncClient(trust_env=False) as client:
        while True:
            with_ws = await _try_page_ws(client, debug_port)
            if with_ws is not None:
                return with_ws
            if loop.time() >= deadline:
                msg = "Browser DevTools endpoint did not expose a page target in time."
                raise TimeoutError(msg)
            await asyncio.sleep(_CDP_POLL_INTERVAL)


async def _try_page_ws(client: httpx.AsyncClient, debug_port: int) -> str | None:
    try:
        response = await client.get(f"http://127.0.0.1:{debug_port}/json")
        targets = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    for target in targets:
        if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
            return str(target["webSocketDebuggerUrl"])
    return None


def _seed_script(auth: MintedWebAuth) -> str:
    """A document-start script that seeds WebK's localStorage on the telegram origin only."""
    seed = build_webk_localstorage(auth)
    return (
        f"if (location.origin === {json.dumps(_WEBK_ORIGIN)}) {{"
        f"  const seed = {json.dumps(seed)};"
        "  for (const key in seed) localStorage.setItem(key, seed[key]);"
        "}"
    )


async def open_account_web(auth: MintedWebAuth, relay_port: int, *, profile_dir: Path) -> None:
    """Launch a signed-in web.telegram.org window for one account, then leave it running.

    Seeds the minted authorization into localStorage over CDP before the first
    telegram document loads, navigates to ``/k/``, closes the CDP socket, and does
    NOT terminate the browser — it is the operator's window.
    """
    make_private_dir(profile_dir)
    browser = find_browser()
    debug_port = _free_port()
    args = build_launch_args(
        user_data_dir=profile_dir,
        relay_port=relay_port,
        debug_port=debug_port,
        url=_LAUNCH_URL,
    )
    await asyncio.create_subprocess_exec(
        str(browser),
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    ws_url = await _discover_page_ws(debug_port)
    session = await CdpSession.connect(ws_url)
    try:
        await session.send_command("Page.enable")
        await session.send_command(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": _seed_script(auth)},
        )
        await session.send_command("Page.navigate", {"url": _WEBK_URL})
    finally:
        await session.aclose()


async def relaunch_account_web(relay_port: int, *, profile_dir: Path) -> None:
    """Reopen an already-signed-in profile through the relay, without re-seeding.

    A repeat click has a persistent profile that already holds WebK's
    localStorage from the first open, so minting again would only spawn another
    'Active Sessions' device. This boots the browser straight at ``/k/`` through
    the account's relay — no CDP socket, no document-start seed — and leaves the
    operator's window running.
    """
    make_private_dir(profile_dir)
    browser = find_browser()
    args = build_launch_args(
        user_data_dir=profile_dir,
        relay_port=relay_port,
        debug_port=_free_port(),
        url=_WEBK_URL,
    )
    await asyncio.create_subprocess_exec(
        str(browser),
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
