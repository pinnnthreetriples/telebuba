"""The per-account browser launcher: pure seams verified, the live parts mocked.

A real browser cannot run in CI, so the launch argv and the browser discovery are
tested directly, and the CDP-driven primitives (:func:`launch_webk_with_hook`,
:func:`latest_login_token`, :func:`page_state`, :func:`type_2fa_password`) run
against a recording fake session — asserting the hook is installed, ``/k/`` is
navigated, the captured token is read, the page is classified and the 2FA password
is typed with real key events. The hand-rolled CDP WebSocket client is exercised for
real against a tiny loopback server (handshake, masking, ping/pong).
"""

from __future__ import annotations

import asyncio
import base64
import json
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from core.web_login import browser
from core.web_login._cdp import CdpSession
from core.web_login.browser import (
    build_launch_args,
    find_browser,
    latest_login_token,
    launch_webk_with_hook,
    page_state,
    relaunch_account_web,
    token_bytes,
    type_2fa_password,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_TIMEOUT = 5.0


# --------------------------------------------------------------------------- helpers


class _RecordingSession:
    """A fake CdpSession: records every command, scripts Runtime.evaluate by expression."""

    def __init__(self, evaluate_values: dict[str, str] | None = None) -> None:
        self.commands: list[tuple[str, dict[str, object]]] = []
        self._evaluate_values = evaluate_values or {}
        self.closed = False

    async def send_command(
        self,
        method: str,
        params: dict[str, object] | None = None,
    ) -> dict[str, object]:
        params = params or {}
        self.commands.append((method, params))
        expr = params.get("expression")
        if method == "Runtime.evaluate" and isinstance(expr, str):
            value = self._evaluate_values.get(expr)
            if value is not None:
                return {"result": {"result": {"type": "string", "value": value}}}
        return {"result": {}}

    async def aclose(self) -> None:
        self.closed = True


class _FakeProc:
    def __init__(self, recorder: dict[str, Any]) -> None:
        self._recorder = recorder

    def terminate(self) -> None:
        self._recorder["terminated"] = True

    def kill(self) -> None:
        self._recorder["killed"] = True

    async def wait(self) -> int:
        self._recorder["waited"] = True
        return 0


# ----------------------------------------------------------------------- launch args


def test_build_launch_args_carries_every_required_flag() -> None:
    args = build_launch_args(
        user_data_dir=Path(r"C:\profiles\acct-1"),
        relay_port=41000,
        debug_port=42000,
        url="about:blank",
    )

    assert r"--user-data-dir=C:\profiles\acct-1" in args
    assert "--proxy-server=http://127.0.0.1:41000" in args
    assert "--remote-debugging-port=42000" in args
    # Origin is scoped to this exact loopback endpoint, never the lifetime-wide "*".
    assert "--remote-allow-origins=http://127.0.0.1:42000" in args
    assert "--remote-allow-origins=*" not in args
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in args
    assert "--disable-features=WebRtcHideLocalIpsWithMdns" in args
    assert "--app=about:blank" in args
    # Loopback is bypassed by default, so no <-loopback> bypass is added.
    assert not any(arg.startswith("--proxy-bypass-list") for arg in args)


# -------------------------------------------------------------------------- browser


def _exists_only(*allowed: Path) -> Callable[[Path], bool]:
    return lambda self: self in allowed


def test_find_browser_prefers_chrome_over_edge(monkeypatch: pytest.MonkeyPatch) -> None:
    chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    monkeypatch.setattr(browser, "_candidate_browsers", lambda: [chrome, edge])
    monkeypatch.setattr(Path, "exists", _exists_only(chrome, edge))

    assert find_browser() == chrome


def test_find_browser_falls_back_to_edge(monkeypatch: pytest.MonkeyPatch) -> None:
    chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    monkeypatch.setattr(browser, "_candidate_browsers", lambda: [chrome, edge])
    monkeypatch.setattr(Path, "exists", _exists_only(edge))

    assert find_browser() == edge


def test_find_browser_raises_when_none_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser, "_candidate_browsers", lambda: [Path(r"C:\nope\chrome.exe")])
    monkeypatch.setattr(Path, "exists", lambda self: False)  # noqa: ARG005

    with pytest.raises(browser.BrowserNotFoundError):
        find_browser()


def test_candidate_browsers_lists_chrome_before_edge() -> None:
    candidates = browser._candidate_browsers()
    assert any(c.name == "chrome.exe" for c in candidates)
    first_chrome = next(i for i, c in enumerate(candidates) if c.name == "chrome.exe")
    first_edge = next(i for i, c in enumerate(candidates) if c.name == "msedge.exe")
    assert first_chrome < first_edge


# --------------------------------------------------------------------------- token_bytes


def test_token_bytes_decodes_base64url_without_padding() -> None:
    raw = bytes(range(20))  # 20 bytes -> base64 needs padding stripped by the hook
    b64url = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    assert "=" not in b64url
    assert token_bytes(b64url) == raw


# -------------------------------------------------------------------- launch_webk_with_hook


@pytest.mark.asyncio
async def test_launch_webk_with_hook_installs_hook_navigates_and_returns_handles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    recorder: dict[str, Any] = {}
    fake_browser = Path(r"C:\fake\chrome.exe")
    profile_dir = tmp_path / "acct-1"
    debug_port = 5555
    relay_port = 41000
    fake_session = _RecordingSession()

    async def _fake_exec(program: str, *args: str, **kwargs: object) -> _FakeProc:
        recorder["exec"] = (program, args, kwargs)
        return _FakeProc(recorder)

    async def _fake_discover(_debug_port: int) -> str:
        return "ws://127.0.0.1:5555/devtools/page/ABC"

    class _FakeCdp:
        @classmethod
        async def connect(cls, ws_url: str) -> _RecordingSession:
            recorder["ws_url"] = ws_url
            return fake_session

    monkeypatch.setattr(browser, "find_browser", lambda: fake_browser)
    monkeypatch.setattr(browser, "_free_port", lambda: debug_port)
    monkeypatch.setattr(browser, "_discover_page_ws", _fake_discover)
    monkeypatch.setattr(browser, "CdpSession", _FakeCdp)
    monkeypatch.setattr(browser.asyncio, "create_subprocess_exec", _fake_exec)

    session, proc = await launch_webk_with_hook(relay_port, profile_dir=profile_dir)

    # Launched with the debug-port hook args build_launch_args produces.
    expected_args = build_launch_args(
        user_data_dir=profile_dir,
        relay_port=relay_port,
        debug_port=debug_port,
        url=browser._LAUNCH_URL,
    )
    program, args, _kwargs = recorder["exec"]
    assert program == str(fake_browser)
    assert list(args) == expected_args
    assert profile_dir.is_dir()

    methods = [method for method, _params in fake_session.commands]
    assert methods == ["Page.enable", "Page.addScriptToEvaluateOnNewDocument", "Page.navigate"]

    _add_method, add_params = fake_session.commands[1]
    assert add_params["source"] == browser.WORKER_HOOK
    _nav_method, nav_params = fake_session.commands[2]
    assert nav_params == {"url": browser._WEBK_URL}

    # Both handles are returned to the caller; the browser is never terminated here.
    assert session is fake_session
    assert isinstance(proc, _FakeProc)
    assert "terminated" not in recorder
    assert "killed" not in recorder


# ------------------------------------------------------------------- latest_login_token


@pytest.mark.asyncio
async def test_latest_login_token_returns_the_freshest_captured() -> None:
    session = _RecordingSession({browser._READ_CAPTURED_EXPR: json.dumps(["tok-a", "tok-b"])})
    assert await latest_login_token(session) == "tok-b"  # ty: ignore[invalid-argument-type]


@pytest.mark.asyncio
async def test_latest_login_token_is_none_when_nothing_captured() -> None:
    session = _RecordingSession({browser._READ_CAPTURED_EXPR: json.dumps([])})
    assert await latest_login_token(session) is None  # ty: ignore[invalid-argument-type]


# -------------------------------------------------------------------------- page_state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("info", "expected"),
    [
        ({"inp": True, "b": ""}, "password"),
        ({"inp": False, "b": "please enter your password to continue"}, "password"),
        ({"inp": False, "b": "scan the qr code with telegram"}, "qr"),
        ({"inp": False, "b": "settings archived chats saved messages contacts"}, "logged_in"),
        ({"inp": False, "b": ""}, "loading"),
    ],
)
async def test_page_state_classifies_the_visible_page(
    info: dict[str, object],
    expected: str,
) -> None:
    session = _RecordingSession({browser._PAGE_STATE_EXPR: json.dumps(info)})
    assert await page_state(session) == expected  # ty: ignore[invalid-argument-type]


@pytest.mark.asyncio
async def test_page_state_is_loading_when_the_probe_yields_no_value() -> None:
    session = _RecordingSession()  # no scripted value -> {"result": {}}
    assert await page_state(session) == "loading"  # ty: ignore[invalid-argument-type]


# --------------------------------------------------------------------- type_2fa_password


@pytest.mark.asyncio
async def test_type_2fa_password_clicks_field_types_and_clicks_submit() -> None:
    session = _RecordingSession(
        {
            browser._INPUT_RECT_EXPR: '{"x":100,"y":200}',
            browser._SUBMIT_RECT_EXPR: '{"x":300,"y":400}',
        }
    )

    await type_2fa_password(session, "pw1")  # ty: ignore[invalid-argument-type]

    key_events = [
        params for method, params in session.commands if method == "Input.dispatchKeyEvent"
    ]
    downs = [e for e in key_events if e["type"] == "keyDown"]
    typed = "".join(str(e.get("text", "")) for e in downs)
    assert typed == "pw1"  # the password chars went in as real key text
    assert {"type": "keyDown", "key": "a", "code": "KeyA", "modifiers": 2} in key_events

    # A real mouse click focuses the visible field, and another submits via Next.
    clicks = [
        (p["x"], p["y"])
        for method, p in session.commands
        if method == "Input.dispatchMouseEvent" and p["type"] == "mousePressed"
    ]
    assert (100, 200) in clicks  # clicked the visible password field
    assert (300, 400) in clicks  # clicked the Next button

    evals = [
        params["expression"] for method, params in session.commands if method == "Runtime.evaluate"
    ]
    assert browser._INPUT_RECT_EXPR in evals
    assert browser._SUBMIT_RECT_EXPR in evals


# ---------------------------------------------------------------- relaunch_account_web


@pytest.mark.asyncio
async def test_relaunch_boots_webk_through_relay_without_a_hook(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    recorder: dict[str, Any] = {}
    fake_browser = Path(r"C:\fake\chrome.exe")
    profile_dir = tmp_path / "acct-1"
    relay_port = 41000

    async def _fake_exec(program: str, *args: str, **kwargs: object) -> _FakeProc:
        recorder["exec"] = (program, args, kwargs)
        return _FakeProc(recorder)

    def _no_cdp(_ws_url: str) -> None:
        recorder["cdp_connect"] = True

    monkeypatch.setattr(browser, "find_browser", lambda: fake_browser)
    monkeypatch.setattr(browser.CdpSession, "connect", _no_cdp)
    monkeypatch.setattr(browser.asyncio, "create_subprocess_exec", _fake_exec)

    await relaunch_account_web(relay_port, profile_dir=profile_dir)

    # Launched with the relay + persistent profile, pointed straight at WebK, with
    # NO DevTools endpoint (no debug port) on the relaunch path.
    expected_args = build_launch_args(
        user_data_dir=profile_dir,
        relay_port=relay_port,
        url=browser._WEBK_URL,
    )
    program, args, _kwargs = recorder["exec"]
    assert program == str(fake_browser)
    assert list(args) == expected_args
    assert profile_dir.is_dir()
    assert not any(arg.startswith("--remote-debugging-port") for arg in args)
    assert not any(arg.startswith("--remote-allow-origins") for arg in args)

    # No CDP on a repeat open, and the operator's window is never killed.
    assert "cdp_connect" not in recorder
    assert "terminated" not in recorder
    assert "killed" not in recorder


# --------------------------------------------------------------- CDP WebSocket client


async def _server_read_client_text(reader: asyncio.StreamReader) -> str:
    """Read one masked client text frame (payloads here are < 126 bytes)."""
    head = await reader.readexactly(2)
    length = head[1] & 0x7F
    mask = await reader.readexactly(4)
    payload = await reader.readexactly(length)
    return bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload)).decode("utf-8")


def _server_text_frame(text: str) -> bytes:
    payload = text.encode("utf-8")
    return bytes([0x81, len(payload)]) + payload


@pytest.mark.asyncio
async def test_cdp_session_round_trip_over_loopback() -> None:
    received: dict[str, Any] = {}

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                b"HTTP/1.1 101 Switching Protocols\r\n"
                b"Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n",
            )
            await writer.drain()
            command = json.loads(await _server_read_client_text(reader))
            received["command"] = command
            # A server ping first, to exercise the client's pong path, then the reply.
            writer.write(bytes([0x89, 0]))
            writer.write(
                _server_text_frame(json.dumps({"id": command["id"], "result": {"ok": True}})),
            )
            await writer.drain()
            await reader.read()  # let the client close (its masked close frame) before we do
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()

    server = await asyncio.start_server(handle, host="127.0.0.1", port=0)
    port = int(server.sockets[0].getsockname()[1])
    try:
        session = await CdpSession.connect(f"ws://127.0.0.1:{port}/devtools/page/ABC")
        response = await asyncio.wait_for(
            session.send_command("Page.navigate", {"url": "https://web.telegram.org/k/"}),
            timeout=_TIMEOUT,
        )
        await session.aclose()
    finally:
        server.close()
        await server.wait_closed()

    assert received["command"]["method"] == "Page.navigate"
    assert received["command"]["params"] == {"url": "https://web.telegram.org/k/"}
    assert response["result"] == {"ok": True}
