"""The per-account browser launcher: pure seams verified, the live parts mocked.

A real browser cannot run in CI, so the launch argv and the browser discovery are
tested directly, and the CDP-driven primitives (:func:`launch_account_web`,
:func:`latest_login_token`, :func:`page_state`, :func:`type_2fa_password`) run
against a recording fake session — asserting the account's fingerprint is applied
to the page BEFORE ``/k/`` is navigated, the QR hook is installed only on a first
open, the captured token is read, the page is classified and the 2FA password is
typed with real key events.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from core.web_login import browser
from core.web_login._targets import TargetDriver
from core.web_login.browser import (
    WebWindow,
    build_launch_args,
    find_browser,
    latest_login_token,
    launch_account_web,
    page_state,
    token_bytes,
    type_2fa_password,
)
from core.web_login.fingerprint import fingerprint_for

if TYPE_CHECKING:
    from collections.abc import Callable

_TIMEOUT = 5.0
_PAGE = "PAGE-1"
_FINGERPRINT = fingerprint_for("acct-1", "DE")


# --------------------------------------------------------------------------- helpers


def attached(session_id: str, kind: str, *, waiting: bool = True) -> dict[str, Any]:
    """A ``Target.attachedToTarget`` event as the browser would deliver it."""
    return {
        "method": "Target.attachedToTarget",
        "params": {
            "sessionId": session_id,
            "targetInfo": {"type": kind, "targetId": session_id},
            "waitingForDebugger": waiting,
        },
    }


class RecordingSession:
    """A fake CdpSession: records every command with its target session id."""

    def __init__(
        self,
        evaluate_values: dict[str, str] | None = None,
        events: list[dict[str, Any]] | None = None,
    ) -> None:
        self.commands: list[tuple[str, dict[str, object], str | None]] = []
        self._evaluate_values = evaluate_values or {}
        self._events = list(events or [])
        self.closed = False

    async def send_command(
        self,
        method: str,
        params: dict[str, object] | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, object]:
        params = params or {}
        self.commands.append((method, params, session_id))
        expr = params.get("expression")
        if method == "Runtime.evaluate" and isinstance(expr, str):
            value = self._evaluate_values.get(expr)
            if value is not None:
                return {"result": {"result": {"type": "string", "value": value}}}
        return {"result": {}}

    async def next_target_event(self, wait_seconds: float) -> dict[str, Any] | None:
        if self._events:
            return self._events.pop(0)
        # Park like the real queue does, so a background driver cannot busy-spin.
        await asyncio.sleep(min(wait_seconds, 0.05))
        return None

    async def aclose(self) -> None:
        self.closed = True

    @property
    def methods(self) -> list[str]:
        return [method for method, _params, _session in self.commands]


class _FakeProc:
    def __init__(self, recorder: dict[str, Any]) -> None:
        self._recorder = recorder
        self.returncode: int | None = None

    def terminate(self) -> None:
        self._recorder["terminated"] = True

    def kill(self) -> None:
        self._recorder["killed"] = True

    async def wait(self) -> int:
        self._recorder["waited"] = True
        return 0


def window_for(session: RecordingSession) -> WebWindow:
    """A window around ``session`` for the primitives that drive an open page."""
    return WebWindow(
        session=session,  # ty: ignore[invalid-argument-type]
        driver=TargetDriver(session, _FINGERPRINT),  # ty: ignore[invalid-argument-type]
        page=_PAGE,
        process=_FakeProc({}),  # ty: ignore[invalid-argument-type]
    )


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


# ------------------------------------------------------------------- launch_account_web


async def _launch(
    monkeypatch: pytest.MonkeyPatch,
    profile_dir: Path,
    *,
    capture_tokens: bool,
) -> tuple[RecordingSession, dict[str, Any], WebWindow]:
    recorder: dict[str, Any] = {}
    fake_browser = Path(r"C:\fake\chrome.exe")
    session = RecordingSession(events=[attached(_PAGE, "page")])

    async def _fake_exec(program: str, *args: str, **kwargs: object) -> _FakeProc:
        recorder["exec"] = (program, args, kwargs)
        return _FakeProc(recorder)

    async def _fake_ws(_debug_port: int) -> str:
        return "ws://127.0.0.1:5555/devtools/browser/ABC"

    class _FakeCdp:
        @classmethod
        async def connect(cls, ws_url: str) -> RecordingSession:
            recorder["ws_url"] = ws_url
            return session

    monkeypatch.setattr(browser, "find_browser", lambda: fake_browser)
    monkeypatch.setattr(browser, "_free_port", lambda: 5555)
    monkeypatch.setattr(browser, "_browser_ws", _fake_ws)
    monkeypatch.setattr(browser, "CdpSession", _FakeCdp)
    monkeypatch.setattr(browser.asyncio, "create_subprocess_exec", _fake_exec)

    window = await launch_account_web(
        41000,
        profile_dir=profile_dir,
        fingerprint=_FINGERPRINT,
        capture_tokens=capture_tokens,
    )
    return session, recorder, window


@pytest.mark.asyncio
async def test_launch_dresses_the_page_before_navigating(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    profile_dir = tmp_path / "acct-1"
    session, recorder, window = await _launch(monkeypatch, profile_dir, capture_tokens=True)
    await window.driver.aclose()

    expected_args = build_launch_args(
        user_data_dir=profile_dir,
        relay_port=41000,
        debug_port=5555,
        url=browser._LAUNCH_URL,
    )
    program, args, _kwargs = recorder["exec"]
    assert program == str(Path(r"C:\fake\chrome.exe"))
    assert list(args) == expected_args
    assert profile_dir.is_dir()
    # Browser-level endpoint: a page-scoped socket would never see a shared worker.
    assert "/devtools/browser/" in recorder["ws_url"]

    methods = session.methods
    navigate = methods.index("Page.navigate")
    # Every identity command lands on the page target BEFORE the first navigation.
    for method in (
        "Emulation.setUserAgentOverride",
        "Emulation.setTimezoneOverride",
        "Emulation.setLocaleOverride",
        "Page.enable",
        "Page.addScriptToEvaluateOnNewDocument",
    ):
        assert methods.index(method) < navigate, method
    assert methods[0] == "Target.setAutoAttach"

    ua_params = next(p for m, p, _s in session.commands if m == "Emulation.setUserAgentOverride")
    assert ua_params["userAgent"] == _FINGERPRINT.user_agent
    assert ua_params["platform"] == _FINGERPRINT.device.nav_platform

    _nav_method, nav_params, nav_session = session.commands[-1]
    assert nav_params == {"url": browser._WEBK_URL}
    assert nav_session == _PAGE
    assert window.page == _PAGE
    # The operator's browser is never terminated by the launcher.
    assert "terminated" not in recorder
    assert "killed" not in recorder


@pytest.mark.asyncio
async def test_launch_installs_the_qr_hook_only_when_capturing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first, _recorder, window = await _launch(monkeypatch, tmp_path / "first", capture_tokens=True)
    await window.driver.aclose()
    sources = [p.get("source") for m, p, _s in first.commands if m.endswith("OnNewDocument")]
    assert browser.WORKER_HOOK in sources

    repeat, _recorder2, window2 = await _launch(
        monkeypatch, tmp_path / "repeat", capture_tokens=False
    )
    await window2.driver.aclose()
    repeat_sources = [
        p.get("source") for m, p, _s in repeat.commands if m.endswith("OnNewDocument")
    ]
    # The page hardening script still goes in; only the token capture is withheld,
    # because accepting a second token would spawn another Active Sessions device.
    assert repeat_sources
    assert browser.WORKER_HOOK not in repeat_sources


@pytest.mark.asyncio
async def test_launched_window_reports_alive() -> None:
    session = RecordingSession()
    window = window_for(session)
    assert window.alive is True
    session.closed = True
    assert window.alive is False


# ------------------------------------------------------------------- latest_login_token


@pytest.mark.asyncio
async def test_latest_login_token_returns_the_freshest_captured() -> None:
    session = RecordingSession({browser._READ_CAPTURED_EXPR: json.dumps(["tok-a", "tok-b"])})
    assert await latest_login_token(window_for(session)) == "tok-b"


@pytest.mark.asyncio
async def test_latest_login_token_is_none_when_nothing_captured() -> None:
    session = RecordingSession({browser._READ_CAPTURED_EXPR: json.dumps([])})
    assert await latest_login_token(window_for(session)) is None


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
    session = RecordingSession({browser._PAGE_STATE_EXPR: json.dumps(info)})
    assert await page_state(window_for(session)) == expected


@pytest.mark.asyncio
async def test_page_state_is_loading_when_the_probe_yields_no_value() -> None:
    session = RecordingSession()  # no scripted value -> {"result": {}}
    assert await page_state(window_for(session)) == "loading"


@pytest.mark.asyncio
async def test_page_primitives_address_the_page_session() -> None:
    session = RecordingSession({browser._PAGE_STATE_EXPR: json.dumps({"inp": False, "b": ""})})
    await page_state(window_for(session))
    assert all(target == _PAGE for _m, _p, target in session.commands)


# --------------------------------------------------------------------- type_2fa_password


@pytest.mark.asyncio
async def test_type_2fa_password_clicks_field_types_and_clicks_submit() -> None:
    session = RecordingSession(
        {
            browser._INPUT_RECT_EXPR: '{"x":100,"y":200}',
            browser._SUBMIT_RECT_EXPR: '{"x":300,"y":400}',
        }
    )

    await type_2fa_password(window_for(session), "pw1")

    key_events = [
        params for method, params, _s in session.commands if method == "Input.dispatchKeyEvent"
    ]
    downs = [e for e in key_events if e["type"] == "keyDown"]
    typed = "".join(str(e.get("text", "")) for e in downs)
    assert typed == "pw1"  # the password chars went in as real key text
    assert {"type": "keyDown", "key": "a", "code": "KeyA", "modifiers": 2} in key_events

    # A real mouse click focuses the visible field, and another submits via Next.
    clicks = [
        (p["x"], p["y"])
        for method, p, _s in session.commands
        if method == "Input.dispatchMouseEvent" and p["type"] == "mousePressed"
    ]
    assert (100, 200) in clicks  # clicked the visible password field
    assert (300, 400) in clicks  # clicked the Next button

    evals = [
        params["expression"]
        for method, params, _s in session.commands
        if method == "Runtime.evaluate"
    ]
    assert browser._INPUT_RECT_EXPR in evals
    assert browser._SUBMIT_RECT_EXPR in evals
