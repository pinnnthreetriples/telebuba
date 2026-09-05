"""The per-account browser launcher: pure seams verified, the live parts mocked.

A real browser cannot run in CI, so the launch argv and the browser discovery are
tested directly, and :func:`launch_account_web` runs against a recording fake session —
asserting the account's fingerprint is applied to the page BEFORE ``/k/`` is navigated,
the QR hook is installed only on a first open, a failure anywhere after the spawn kills
the browser instead of orphaning it, and the port pick is serialized against a
concurrent open. The primitives that drive an open page live in ``test_web_login_page``.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from core.web_login import browser, fingerprint
from core.web_login._cdp import CdpError
from core.web_login._targets import TargetDriver
from core.web_login.browser import (
    WebWindow,
    account_profile_dir,
    build_launch_args,
    find_browser,
    launch_account_web,
    token_bytes,
)
from tests.core.web_login_helpers import (
    FINGERPRINT,
    PAGE,
    FakeProc,
    RecordingSession,
    attached,
    window_for,
    wire_launch,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_FINGERPRINT = FINGERPRINT


# ----------------------------------------------------------------------- launch args


def _args() -> list[str]:
    return build_launch_args(
        user_data_dir=Path(r"C:\profiles\acct-1"),
        relay_port=41000,
        debug_port=42000,
        url="about:blank",
        fingerprint=_FINGERPRINT,
    )


def test_build_launch_args_carries_every_required_flag() -> None:
    args = _args()

    assert r"--user-data-dir=C:\profiles\acct-1" in args
    assert "--proxy-server=http://127.0.0.1:41000" in args
    assert "--remote-debugging-port=42000" in args
    # Origin is scoped to this exact loopback endpoint, never the lifetime-wide "*".
    assert "--remote-allow-origins=http://127.0.0.1:42000" in args
    assert "--remote-allow-origins=*" not in args
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in args
    assert "--app=about:blank" in args
    # Loopback is bypassed by default, so no <-loopback> bypass is added.
    assert not any(arg.startswith("--proxy-bypass-list") for arg in args)


def test_launch_args_keep_the_mdns_webrtc_mitigation_on() -> None:
    """WebRtcHideLocalIpsWithMdns replaces host ICE candidates with <uuid>.local.

    Turning it off puts this desktop's LAN address into the SDP of every account's
    window (the UDP policy only covers UDP; TCP host candidates still carry it) — the
    same 192.168.x.y in all of them, a perfect cross-account correlator.
    """
    assert not any("WebRtcHideLocalIpsWithMdns" in arg for arg in _args())


def test_launch_args_keep_chrome_background_networking_off_the_proxy() -> None:
    """--proxy-server has no bypass, so Chrome's own chatter goes out via the account.

    Component updates, the optimization guide, Safe Browsing fetches and domain
    reliability each open a fresh upstream tunnel (TCP + greeting + auth + CONNECT,
    ~600 ms at a residential RTT) while WebK is still pulling its bundle — and they
    egress from the account's exit IP. One umbrella flag covers all of them.
    """
    assert "--disable-background-networking" in _args()
    # NOT --disable-quic: with an HTTP proxy Chrome already tunnels over TCP CONNECT,
    # so it would only change the ALPN offer and make the client more distinctive.
    assert "--disable-quic" not in _args()


def test_the_devtools_endpoint_is_polled_tightly() -> None:
    """A refused connect on loopback costs microseconds, so this interval is dead time.

    It sits in front of the operator's very first window on every cold launch.
    """
    assert browser._CDP_POLL_INTERVAL <= 0.05


def test_launch_args_carry_the_network_layer_identity() -> None:
    """Emulation.setUserAgentOverride is page-scoped and never reaches a shared worker.

    Without these the worker's own requests — the very connection whose initConnection
    claims a Mac — go out with the operator's real Chrome/Windows headers.
    """
    args = _args()

    assert f"--user-agent={_FINGERPRINT.user_agent}" in args
    assert f"--lang={_FINGERPRINT.locale}" in args


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


# ----------------------------------------------------------------------- profile dir


def test_account_profile_dir_is_absolute() -> None:
    """A relative --user-data-dir does not isolate anything.

    Chrome hands its command line to whatever Chrome is already running and exits 0,
    so the account's window would open in the operator's own browser, on the
    operator's own IP. The sessions dir is relative in a default deployment, so this
    is the load-bearing assertion, not a formality.
    """
    profile = account_profile_dir("acct-1")

    assert profile.is_absolute()
    assert profile.name == "acct-1"
    assert profile.parent.name == "web_profiles"


# --------------------------------------------------------------------- devtools wait


@pytest.mark.asyncio
async def test_browser_ws_reports_a_handoff_rather_than_waiting_out_the_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def _never(_client: object, _debug_port: int) -> str | None:
        return None

    monkeypatch.setattr(browser, "_try_browser_ws", _never)
    exited = FakeProc({})
    exited.returncode = 0  # Chrome forwarded its argv to a running instance and quit

    with pytest.raises(browser.BrowserStartError):
        await browser._browser_ws(5555, exited, tmp_path)  # ty: ignore[invalid-argument-type]


@pytest.mark.asyncio
async def test_the_real_browser_build_is_read_off_json_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The claimed Chrome version has to be the installed one.

    JS feature detection is not overridden, so a UA claiming a milestone the binary is
    not is directly observable; /json/version is the only place that answer lives.
    """
    monkeypatch.setattr(fingerprint, "_observed", {})

    class _Response:
        @staticmethod
        def json() -> dict[str, str]:
            return {
                "Browser": "Chrome/148.0.7222.0",
                "webSocketDebuggerUrl": "ws://127.0.0.1:5555/devtools/browser/ABC",
            }

    class _Client:
        @staticmethod
        async def get(_url: str) -> _Response:
            return _Response()

    url = await browser._try_browser_ws(_Client(), 5555)  # ty: ignore[invalid-argument-type]

    assert url == "ws://127.0.0.1:5555/devtools/browser/ABC"
    identity = fingerprint.fingerprint_for("acct-1", "DE")
    assert identity.chrome_full == "148.0.7222.0"
    # The UA string is reduced (Chrome/<major>.0.0.0), so the milestone is what shows.
    assert "Chrome/148.0.0.0" in identity.user_agent


def test_an_endpoint_from_another_browser_is_rejected(tmp_path: Path) -> None:
    """DevToolsActivePort names the target of the Chrome that owns THIS profile.

    Two accounts opening at once can be handed the same free port; attaching to the
    other one's browser would re-dress its page with the wrong fingerprint and
    navigate its window. A disagreeing file is proof the endpoint is not ours.
    """
    (tmp_path / browser._ACTIVE_PORT_FILE).write_text(
        "5555\n/devtools/browser/OURS\n", encoding="utf-8"
    )

    assert browser._endpoint_is_foreign(tmp_path, "ws://127.0.0.1:5555/devtools/browser/THEIRS")
    assert not browser._endpoint_is_foreign(tmp_path, "ws://127.0.0.1:5555/devtools/browser/OURS")


def test_a_missing_active_port_file_never_rejects_an_endpoint(tmp_path: Path) -> None:
    """One-sided on purpose: absence proves nothing, and must not strand a launch."""
    assert not browser._endpoint_is_foreign(tmp_path, "ws://127.0.0.1:5555/devtools/browser/ABC")


# ------------------------------------------------------------------- launch_account_web


async def _launch(
    monkeypatch: pytest.MonkeyPatch,
    profile_dir: Path,
    *,
    capture_tokens: bool,
) -> tuple[RecordingSession, dict[str, Any], WebWindow]:
    recorder: dict[str, Any] = {}
    session = RecordingSession(events=[attached(PAGE, "page")])
    wire_launch(monkeypatch, recorder, session)

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
        fingerprint=_FINGERPRINT,
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
    assert nav_session == PAGE
    assert window.page == PAGE
    # The operator's browser is never terminated by a launch that worked.
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
    assert browser.QR_CAPTURE_HOOK in sources

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
    assert browser.QR_CAPTURE_HOOK not in repeat_sources


def test_the_qr_hook_exposes_its_teardown_and_never_fires_it_itself() -> None:
    """``window.__cap`` holds live ``auth.loginToken`` values, so it must not outlive the login.

    But nothing INSIDE the page can tell when the login is done. The version that tried
    watched for WebK's chat-shell markers on a 2 s timer — and those markers are in the
    document, merely zero-sized, from the first paint, which is exactly why the page-state
    probe filters them on visibility. So the timer fired on the QR screen, deleted the
    token array seconds after load, and no login token was ever captured: measured live
    as QR at 42 s, nothing captured, no login, and reproduced against a real Chrome.
    """
    hook = browser.QR_CAPTURE_HOOK

    # No in-page clock of any kind: the one moment that is safe is known only to Python.
    assert "setInterval" not in hook
    assert "setTimeout" not in hook
    assert "#column-center" not in hook
    # The teardown itself is kept, exposed for the driver to fire once login completed.
    assert "window.__capOff=" in hook
    assert "window.__cap.length=0" in hook
    assert "delete window.__cap" in hook
    # What CAN be undone is undone: the prototypes this hook replaced.
    assert "EventTarget.prototype.addEventListener=ael;" in hook
    assert "EventTarget.prototype.removeEventListener=rel;" in hook
    assert "MessagePort.prototype.postMessage=mp;" in hook
    assert "Object.defineProperty(P,'onmessage',dd);" in hook


@pytest.mark.asyncio
async def test_launched_window_reports_alive() -> None:
    """Both halves matter: this is what decides raise-vs-relaunch on the next click.

    A window whose PROCESS is gone but whose socket object still looks open would be
    "raised" — focus would fail, and only then would the relaunch happen.
    """
    session = RecordingSession()
    window = window_for(session)
    assert window.alive is True

    window.process.kill()  # the operator closed the browser
    assert window.process.returncode is not None
    assert window.alive is False

    fresh = window_for(RecordingSession())
    fresh.session.closed = True
    assert fresh.alive is False


# ------------------------------------------------------- failures after the spawn


@pytest.mark.asyncio
@pytest.mark.parametrize("failing", ["ws", "connect", "first_page", "navigate"])
async def test_a_failure_after_the_spawn_kills_the_browser(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failing: str,
) -> None:
    """An orphaned Chrome is the worst outcome this module has.

    Nobody holds a DevTools client for it, so Chrome has already stripped the
    fingerprint and it is sitting on Telegram as the operator's real machine; it also
    keeps the account's --user-data-dir claimed, so every later open for that account
    fails as a hand-off until a human finds and closes the window.

    ``navigate`` is the only step that runs AFTER ``driver.start()``, so it is the only
    case in which the cleanup has a live driver task to cancel: without it the
    driver-closing half of ``_attach`` is never executed by any test at all.
    """
    recorder: dict[str, Any] = {}
    session = RecordingSession(
        events=[attached(PAGE, "page")],
        fail_on="Page.navigate" if failing == "navigate" else None,
    )
    proc = FakeProc(recorder)
    wire_launch(monkeypatch, recorder, session, proc=proc)

    boom = RuntimeError("boom")
    if failing == "ws":

        async def _bad_ws(_port: int, _process: object, _profile: Path) -> str:
            raise boom

        monkeypatch.setattr(browser, "_browser_ws", _bad_ws)
    elif failing == "connect":

        class _DeadCdp:
            @staticmethod
            async def connect(_ws_url: str) -> RecordingSession:
                raise boom

        monkeypatch.setattr(browser, "CdpSession", _DeadCdp)
    elif failing == "first_page":

        async def _bad_page(_self: object) -> str:
            raise boom

        monkeypatch.setattr(browser.TargetDriver, "first_page_session", _bad_page)

    with pytest.raises((RuntimeError, CdpError)):
        await launch_account_web(
            41000,
            profile_dir=tmp_path / "acct-1",
            fingerprint=_FINGERPRINT,
            capture_tokens=True,
        )

    assert recorder["killed"] is True
    assert recorder["waited"] is True
    # Whatever we had attached is dropped too, so no socket outlives the process.
    if failing in {"first_page", "navigate"}:
        assert session.closed is True


# --------------------------------------------------------------------------- kill


@pytest.mark.asyncio
async def test_kill_ends_the_process_not_just_the_socket(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Shutdown must not leave a window pointed at a relay port it is about to free.

    A leftover browser keeps --proxy-server on that loopback port; after a restart a
    DIFFERENT account's relay can bind it, and the window then egresses through the
    wrong account's proxy — an invisible cross-account IP correlation.
    """
    profile = tmp_path / "acct-1"
    session, recorder, window = await _launch(monkeypatch, profile, capture_tokens=False)

    await window.kill()

    assert session.closed is True
    assert recorder["killed"] is True
    assert recorder["waited"] is True


@pytest.mark.asyncio
async def test_kill_ends_the_process_even_when_the_socket_close_raises() -> None:
    """``aclose`` can raise: a pump task that ended with an unexpected exception.

    Two bare statements would then never reach the process kill — and every caller
    wraps ``kill`` in ``suppress(Exception)``, so the orphaned browser is silent: it
    still holds the profile and still points --proxy-server at a port about to be
    handed to another account's relay.
    """
    recorder: dict[str, Any] = {}
    window = WebWindow(
        session=RecordingSession(fail_close=True),  # ty: ignore[invalid-argument-type]
        driver=TargetDriver(RecordingSession(), _FINGERPRINT),  # ty: ignore[invalid-argument-type]
        page=PAGE,
        process=FakeProc(recorder),  # ty: ignore[invalid-argument-type]
    )

    with pytest.raises(CdpError):
        await window.kill()

    assert recorder["killed"] is True
    assert recorder["waited"] is True
