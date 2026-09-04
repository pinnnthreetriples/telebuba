"""Launch a per-account Chrome/Edge window that boots web.telegram.org/k/ signed in.

The browser is pointed at the account's :class:`LocalProxyRelay` (a credential-free
loopback proxy) and given an isolated persistent per-account profile. On the first
open we inject a document-start hook that captures WebK's own QR ``auth.loginToken``
into ``window.__cap``; the caller accepts that token with the account's authorized
session so WebK completes its OWN login (no storage injection). The operator's window
is then left running; the relay lifecycle is the caller's, not ours.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
from pathlib import Path

import httpx

from core.config import settings
from core.secure_paths import make_private_dir
from core.web_login._cdp import CdpSession

_WEBK_URL = "https://web.telegram.org/k/"
_LAUNCH_URL = "about:blank"
_PROFILE_SUBDIR = "web_profiles"
_CDP_READY_TIMEOUT = 20.0
_CDP_POLL_INTERVAL = 0.25
# CDP real-key typing gap between characters, so WebK's field handlers keep up.
_KEY_GAP_SECONDS = 0.02
_CTRL_MODIFIER = 2
# A non-trivial page body means WebK booted past the QR/password screens.
_MIN_LOGGED_IN_BODY = 5

# Injected at document-start on every frame: hooks the Web Worker / MessagePort
# message paths and captures WebK's own QR ``auth.loginToken`` (base64url) into
# ``window.__cap``. Pure page script — no MTProto. Kept byte-for-byte as the build
# that was validated live (WebK build 675); split only for line length.
WORKER_HOOK = (
    "window.__cap=[];\n"
    "function u8(x){return x instanceof Uint8Array?x:Array.isArray(x)?"
    "Uint8Array.from(x):(x&&x.buffer?new Uint8Array(x.buffer):null);}\n"
    "function b(u){return btoa(String.fromCharCode.apply(null,u))"
    r".replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');}"
    "\n"
    "function scan(o,d,s){if(o==null||d>6||typeof o!=='object'||s.has(o))"
    "return;s.add(o);\n"
    " try{if(o._==='auth.loginToken'&&o.token!=null){const v=u8(o.token);"
    "if(v)window.__cap.push(b(v));}}catch(e){}\n"
    " for(const k in o){try{scan(o[k],d+1,s);}catch(e){}}}\n"
    "function w(fn){return function(ev){try{scan(ev&&ev.data,0,new WeakSet());}"
    "catch(e){}return fn.apply(this,arguments);};}\n"
    "for(const P of [MessagePort.prototype,Worker.prototype]){"
    "const a=P.addEventListener;\n"
    " P.addEventListener=function(t,fn,...r){if(t==='message'&&"
    "typeof fn==='function')fn=w(fn);return a.call(this,t,fn,...r);};\n"
    " const dd=Object.getOwnPropertyDescriptor(P,'onmessage');\n"
    " if(dd&&dd.set)Object.defineProperty(P,'onmessage',{configurable:true,"
    "get(){return dd.get.call(this);},set(fn){dd.set.call(this,"
    "typeof fn==='function'?w(fn):fn);}});}\n"
    "const mp=MessagePort.prototype.postMessage;"
    "MessagePort.prototype.postMessage=function(m,...r){try{scan(m,0,new WeakSet());}"
    "catch(e){}return mp.call(this,m,...r);};\n"
)

# In-page probes: read the captured tokens, and classify the visible page.
_READ_CAPTURED_EXPR = "JSON.stringify(window.__cap||[])"
_PAGE_STATE_EXPR = (
    "JSON.stringify({inp: !!document.querySelector('input[type=password]'), "
    "b: (document.body?document.body.innerText:'').toLowerCase()"
    ".replace(/\\s+/g,' ').slice(0,300)})"
)
# WebK's controlled password input only accepts typed characters after a REAL mouse
# click (a JS ``.focus()`` leaves its React state empty, so the submit sends a blank
# password). It also renders a hidden decoy ``input[type=password]`` alongside the
# visible one, so we target the wide, visible input by its bounding width and click
# both it and the Next button at their on-screen centres. These return the element
# centre as JSON (or '' when absent).
_INPUT_RECT_EXPR = (
    "(()=>{const i=[...document.querySelectorAll('input')]"
    ".find(i=>i.getBoundingClientRect().width>200);if(!i)return '';"
    "const r=i.getBoundingClientRect();"
    "return JSON.stringify({x:r.x+r.width/2,y:r.y+r.height/2});})()"
)
_SUBMIT_RECT_EXPR = (
    "(()=>{const b=[...document.querySelectorAll('button')]"
    ".find(b=>/next|\u0434\u0430\u043b\u0435\u0435|\u0432\u043e\u0439\u0442\u0438|"
    "log ?in/i.test(b.textContent||''));if(!b)return '';"
    "const r=b.getBoundingClientRect();"
    "return JSON.stringify({x:r.x+r.width/2,y:r.y+r.height/2});})()"
)
_CLICK_SETTLE_SECONDS = 0.2


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
    url: str,
    debug_port: int | None = None,
) -> list[str]:
    """The Chromium argv: isolated profile, loopback proxy, WebRTC guards, app mode.

    The DevTools endpoint is emitted ONLY when ``debug_port`` is given (the first-open
    hook path). Its allow-origin is scoped to that exact loopback origin rather than
    the lifetime-wide ``*``. The relaunch path passes no port, so its window opens with
    no DevTools endpoint at all.
    """
    debug_flags = (
        [
            f"--remote-debugging-port={debug_port}",
            # Recent Chrome refuses the CDP WebSocket without an allowed origin;
            # scope it to this endpoint instead of disabling the check with "*".
            f"--remote-allow-origins=http://127.0.0.1:{debug_port}",
        ]
        if debug_port is not None
        else []
    )
    return [
        f"--user-data-dir={user_data_dir}",
        f"--proxy-server=http://127.0.0.1:{relay_port}",
        # Keep WebRTC from leaking the real IP around the proxy.
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--disable-features=WebRtcHideLocalIpsWithMdns",
        *debug_flags,
        "--no-first-run",
        "--no-default-browser-check",
        "--no-service-autorun",
        "--disable-sync",
        f"--app={url}",
    ]


def account_profile_dir(account_id: str) -> Path:
    """Per-account persistent profile dir, sibling to the sessions dir. Persists between clicks."""
    return settings.telegram.session_dir.with_name(_PROFILE_SUBDIR) / account_id


def token_bytes(b64url: str) -> bytes:
    """Decode a base64url login token (as the worker hook captured it) to raw bytes."""
    return base64.urlsafe_b64decode(b64url + "=" * (-len(b64url) % 4))


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


async def launch_webk_with_hook(
    relay_port: int,
    *,
    profile_dir: Path,
) -> tuple[CdpSession, asyncio.subprocess.Process]:
    """Launch WebK through the relay with the login-token capture hook installed.

    Boots Chrome at ``about:blank`` in ``--app`` mode with a DevTools endpoint,
    installs :data:`WORKER_HOOK` as a document-start script, then navigates to
    ``/k/``. The caller owns both handles: it drives login over the returned session
    and leaves the browser process running for the operator.
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
    proc = await asyncio.create_subprocess_exec(
        str(browser),
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    ws_url = await _discover_page_ws(debug_port)
    session = await CdpSession.connect(ws_url)
    await session.send_command("Page.enable")
    await session.send_command(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": WORKER_HOOK},
    )
    await session.send_command("Page.navigate", {"url": _WEBK_URL})
    return session, proc


def _evaluate_value(response: dict) -> object:
    """Pull ``result.result.value`` off a Runtime.evaluate response, or ``None``."""
    try:
        return response["result"]["result"]["value"]
    except (KeyError, TypeError):
        return None


async def latest_login_token(session: CdpSession) -> str | None:
    """The freshest ``auth.loginToken`` the hook captured (base64url), or ``None``."""
    response = await session.send_command(
        "Runtime.evaluate",
        {"expression": _READ_CAPTURED_EXPR, "returnByValue": True},
    )
    value = _evaluate_value(response)
    if not isinstance(value, str):
        return None
    try:
        tokens = json.loads(value)
    except ValueError:
        return None
    return tokens[-1] if tokens else None


async def page_state(session: CdpSession) -> str:
    """Classify the WebK page as ``password`` / ``qr`` / ``logged_in`` / ``loading``."""
    response = await session.send_command(
        "Runtime.evaluate",
        {"expression": _PAGE_STATE_EXPR, "returnByValue": True},
    )
    value = _evaluate_value(response)
    if not isinstance(value, str):
        return "loading"
    try:
        info = json.loads(value)
    except ValueError:
        return "loading"
    body = info.get("b", "")
    if info.get("inp") or "enter your password" in body or "additional password" in body:
        return "password"
    if "qr code" in body or "scan with telegram" in body:
        return "qr"
    if len(body) > _MIN_LOGGED_IN_BODY:
        return "logged_in"
    return "loading"


async def _dispatch_key(session: CdpSession, event: dict[str, object]) -> None:
    await session.send_command("Input.dispatchKeyEvent", event)


async def _click_center(session: CdpSession, rect_expr: str) -> bool:
    """Real-mouse-click the centre of the element ``rect_expr`` locates; False if none."""
    raw = await session.send_command(
        "Runtime.evaluate", {"expression": rect_expr, "returnByValue": True}
    )
    value = _evaluate_value(raw)
    if not isinstance(value, str) or not value:
        return False
    point = json.loads(value)
    for kind in ("mousePressed", "mouseReleased"):
        await session.send_command(
            "Input.dispatchMouseEvent",
            {
                "type": kind,
                "x": point["x"],
                "y": point["y"],
                "button": "left",
                "clickCount": 1,
                "buttons": 1,
            },
        )
    return True


async def type_2fa_password(session: CdpSession, password: str) -> None:
    """Type the account's 2FA password into WebK's field and submit. Never logged.

    Real-mouse-clicks the visible password input to focus it (a JS focus leaves WebK's
    controlled input empty), clears it (Ctrl+A, Delete), types each character with real
    CDP key events, then real-mouse-clicks the Next button.
    """
    if not await _click_center(session, _INPUT_RECT_EXPR):
        return
    await asyncio.sleep(_CLICK_SETTLE_SECONDS)
    for kind in ("keyDown", "keyUp"):
        await _dispatch_key(
            session, {"type": kind, "key": "a", "code": "KeyA", "modifiers": _CTRL_MODIFIER}
        )
    for kind in ("keyDown", "keyUp"):
        await _dispatch_key(session, {"type": kind, "key": "Delete", "code": "Delete"})
    for char in password:
        await _dispatch_key(session, {"type": "keyDown", "text": char, "key": char})
        await _dispatch_key(session, {"type": "keyUp", "key": char})
        await asyncio.sleep(_KEY_GAP_SECONDS)
    await _click_center(session, _SUBMIT_RECT_EXPR)


async def relaunch_account_web(relay_port: int, *, profile_dir: Path) -> None:
    """Reopen an already-signed-in profile through the relay, without the hook.

    A repeat click has a persistent profile that already holds WebK's session from
    the first open, so accepting a token again would only spawn another 'Active
    Sessions' device. This boots the browser straight at ``/k/`` through the account's
    relay — no CDP socket, no document-start hook, no DevTools endpoint — and leaves
    the operator's window running.
    """
    make_private_dir(profile_dir)
    browser = find_browser()
    args = build_launch_args(
        user_data_dir=profile_dir,
        relay_port=relay_port,
        url=_WEBK_URL,
    )
    await asyncio.create_subprocess_exec(
        str(browser),
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
