"""The per-account browser launcher: pure seams verified, the live parts mocked.

A real browser cannot run in CI, so the launch argv, the WebK localStorage map and
the browser discovery are tested directly, and :func:`open_account_web` runs with
``create_subprocess_exec``, the DevTools discovery and the CDP session all faked —
asserting it launches with the right args, seeds the authorization, navigates to
``/k/`` and never kills the operator's window. The hand-rolled CDP WebSocket client
is exercised for real against a tiny loopback server (handshake, masking, ping/pong).
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from core.telegram_client._web_login import MintedWebAuth
from core.web_login import browser
from core.web_login._cdp import CdpSession
from core.web_login.browser import (
    build_launch_args,
    find_browser,
    open_account_web,
    relaunch_account_web,
)
from core.web_login.storage import build_webk_localstorage

if TYPE_CHECKING:
    from collections.abc import Callable

_KEY = bytes([0xAB]) * 256
_KEY_HEX = "ab" * 256
_SALT = bytes([1, 2, 3, 4, 5, 6, 7, 8])
_SALT_HEX = "0102030405060708"
_USER_ID = 123456789
_AUTH = MintedWebAuth(dc_id=2, auth_key=_KEY, server_salt=_SALT, user_id=_USER_ID)
_AUTH_NO_SALT = MintedWebAuth(dc_id=2, auth_key=_KEY, server_salt=None, user_id=_USER_ID)
_TIMEOUT = 5.0


# --------------------------------------------------------------------------- storage


def test_build_webk_localstorage_exact_values() -> None:
    store = build_webk_localstorage(_AUTH)

    assert store["dc"] == "2"
    assert store["number_of_accounts"] == "1"  # WebK's session-present gate
    assert store["dc2_auth_key"] == f'"{_KEY_HEX}"'
    assert len(_KEY_HEX) == 512
    assert store["dc2_server_salt"] == f'"{_SALT_HEX}"'
    assert store["auth_key_fingerprint"] == '"abababab"'  # first 8 chars of the key hex
    assert store["server_time_offset"] == "0"

    user_auth = json.loads(store["user_auth"])
    assert user_auth["id"] == _USER_ID
    assert user_auth["dcID"] == 2
    assert isinstance(user_auth["date"], int)

    account = json.loads(store["account1"])
    assert account == {
        "userId": _USER_ID,
        "dcId": 2,
        "dc2_auth_key": _KEY_HEX,
        "dc2_server_salt": _SALT_HEX,
        "auth_key_fingerprint": "abababab",
    }


def test_build_webk_localstorage_omits_salt_when_unknown() -> None:
    store = build_webk_localstorage(_AUTH_NO_SALT)

    assert "dc2_server_salt" not in store
    account = json.loads(store["account1"])
    assert "dc2_server_salt" not in account
    assert account["dc2_auth_key"] == _KEY_HEX


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


# ------------------------------------------------------------------- open_account_web


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


def _fake_session_class(recorder: dict[str, Any]) -> type:
    class _FakeSession:
        @classmethod
        async def connect(cls, ws_url: str) -> _FakeSession:
            recorder["ws_url"] = ws_url
            return cls()

        async def send_command(
            self,
            method: str,
            params: dict[str, object] | None = None,
        ) -> dict[str, object]:
            recorder.setdefault("commands", []).append((method, params))
            if method == "Runtime.evaluate":
                # The seed-applied poll: report the marker present on first check.
                return {
                    "id": len(recorder["commands"]),
                    "result": {"result": {"type": "boolean", "value": True}},
                }
            return {"id": len(recorder["commands"]), "result": {}}

        async def aclose(self) -> None:
            recorder["closed"] = True

    return _FakeSession


@pytest.mark.asyncio
async def test_open_account_web_seeds_navigates_and_leaves_browser(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    recorder: dict[str, Any] = {}
    fake_browser = Path(r"C:\fake\chrome.exe")
    profile_dir = tmp_path / "acct-1"
    debug_port = 5555
    relay_port = 41000

    async def _fake_exec(program: str, *args: str, **kwargs: object) -> _FakeProc:
        recorder["exec"] = (program, args, kwargs)
        return _FakeProc(recorder)

    async def _fake_discover(_debug_port: int) -> str:
        return "ws://127.0.0.1:5555/devtools/page/ABC"

    monkeypatch.setattr(browser, "find_browser", lambda: fake_browser)
    monkeypatch.setattr(browser, "_free_port", lambda: debug_port)
    monkeypatch.setattr(browser, "_discover_page_ws", _fake_discover)
    monkeypatch.setattr(browser, "CdpSession", _fake_session_class(recorder))
    monkeypatch.setattr(browser.asyncio, "create_subprocess_exec", _fake_exec)

    await open_account_web(_AUTH, relay_port, profile_dir=profile_dir)

    # (a) launched with exactly the args build_launch_args produces.
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

    methods = [method for method, _params in recorder["commands"]]
    assert methods == [
        "Page.enable",
        "Page.addScriptToEvaluateOnNewDocument",
        "Page.navigate",
        "Runtime.evaluate",
    ]

    # (b) the seed script carries the authorization's localStorage values.
    _add_method, add_params = recorder["commands"][1]
    source = add_params["source"]
    assert _KEY_HEX in source
    assert "abababab" in source
    assert browser._WEBK_ORIGIN in source

    # (c) navigate goes to the WebK client.
    _nav_method, nav_params = recorder["commands"][2]
    assert nav_params == {"url": browser._WEBK_URL}

    # (d) the CDP socket is closed but the browser is never terminated.
    assert recorder["closed"] is True
    assert "terminated" not in recorder
    assert "killed" not in recorder
    assert "waited" not in recorder


# ---------------------------------------------------------------- relaunch_account_web


@pytest.mark.asyncio
async def test_relaunch_boots_webk_through_relay_without_seeding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    recorder: dict[str, Any] = {}
    fake_browser = Path(r"C:\fake\chrome.exe")
    profile_dir = tmp_path / "acct-1"
    debug_port = 5555
    relay_port = 41000

    async def _fake_exec(program: str, *args: str, **kwargs: object) -> _FakeProc:
        recorder["exec"] = (program, args, kwargs)
        return _FakeProc(recorder)

    def _no_cdp(_ws_url: str) -> None:
        recorder["cdp_connect"] = True

    monkeypatch.setattr(browser, "find_browser", lambda: fake_browser)
    monkeypatch.setattr(browser, "_free_port", lambda: debug_port)
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

    # No CDP seed on a repeat open, and the operator's window is never killed.
    assert "cdp_connect" not in recorder
    assert "terminated" not in recorder
    assert "killed" not in recorder
    assert "waited" not in recorder


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
