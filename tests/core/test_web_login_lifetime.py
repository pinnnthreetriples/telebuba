"""What the spawned browser inherits, and that it never outlives the backend.

The environment and the Job Object binding are checked at the launch seam with the
recorded fakes; the Job Object itself is exercised for real on Windows against a
harmless child Python. The orphan sweep runs against a real loopback endpoint so the
ownership proof — not a mock of it — decides what is closed.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import TYPE_CHECKING, Any

import pytest

from core.web_login import _lifetime, browser
from core.web_login._lifetime import browser_env, close_orphaned_browsers
from core.web_login.browser import launch_account_web
from tests.core.web_login_helpers import FINGERPRINT, PAGE, RecordingSession, attached, wire_launch

if TYPE_CHECKING:
    from pathlib import Path

_APP_SETTINGS = {
    "TELEGRAM__API_HASH": "canary-api-hash",
    "AUTH__SECRET": "canary-auth-secret",
    "DB__URL": "sqlite:///canary.db",
    "PROXY__DEFAULT": "socks5://canary",
}


def _with_app_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The backend's environment as ``load_dotenv`` leaves it, beside what Chrome needs."""
    for key, value in _APP_SETTINGS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    monkeypatch.setenv("TEMP", r"C:\Temp")


# ------------------------------------------------------------------- environment


def test_the_browser_environment_carries_no_app_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_app_settings(monkeypatch)

    env = browser_env()

    assert not set(_APP_SETTINGS) & {key.upper() for key in env}
    assert not set(_APP_SETTINGS.values()) & set(env.values())
    assert env["SYSTEMROOT"] == r"C:\Windows"
    assert env["TEMP"] == r"C:\Temp"


@pytest.mark.asyncio
async def test_the_launch_hands_chrome_the_allow_listed_environment_and_ties_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Without ``env=`` Chrome inherits every value ``.env`` loaded into this process."""
    _with_app_settings(monkeypatch)
    recorder: dict[str, Any] = {}
    wire_launch(monkeypatch, recorder, RecordingSession(events=[attached(PAGE, "page")]))

    window = await launch_account_web(
        41000, profile_dir=tmp_path / "acct-1", fingerprint=FINGERPRINT, capture_tokens=False
    )
    await window.driver.aclose()

    _program, _args, kwargs = recorder["exec"]
    env = kwargs["env"]
    assert env["SYSTEMROOT"] == r"C:\Windows"
    assert not set(_APP_SETTINGS) & {key.upper() for key in env}
    # Bound to the backend's lifetime straight after the spawn.
    assert recorder["tied"] == window.process.pid


@pytest.mark.asyncio
async def test_a_browser_that_cannot_be_tied_is_killed_not_used(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """One the backend cannot take down with it would outlive a crash undressed."""
    recorder: dict[str, Any] = {}
    wire_launch(monkeypatch, recorder, RecordingSession())

    def _refuse(_pid: int) -> None:
        msg = "access denied"
        raise OSError(msg)

    monkeypatch.setattr(browser, "tie_to_backend", _refuse)

    with pytest.raises(OSError, match="access denied"):
        await launch_account_web(
            41000, profile_dir=tmp_path / "acct-1", fingerprint=FINGERPRINT, capture_tokens=True
        )

    assert recorder["killed"] is True
    assert browser._ports_in_flight == set()


# -------------------------------------------------------------------- Job Object


@pytest.mark.asyncio
async def test_closing_the_job_ends_the_process_tied_to_it() -> None:
    """The kernel closes the backend's job handle however the backend ends."""
    if sys.platform != "win32":
        pytest.skip("Job Objects are Windows-only")
    import ctypes  # noqa: PLC0415 - Windows-only

    child = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(60)"
    )
    job = _lifetime._create_kill_on_close_job()
    try:
        _lifetime._assign_to_job(job, child.pid)
    finally:
        ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(job))
    try:
        await asyncio.wait_for(child.wait(), 10)
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()
    assert child.returncode is not None


def test_tying_is_a_no_op_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_lifetime.sys, "platform", "linux")
    monkeypatch.setattr(_lifetime, "_job", {})

    _lifetime.tie_to_backend(4242)

    assert _lifetime._job == {}


# ------------------------------------------------------------------ orphan sweep


async def _serve_version(ws_url: str) -> asyncio.Server:
    """A loopback ``/json/version`` endpoint answering with ``ws_url``."""
    body = json.dumps({"webSocketDebuggerUrl": ws_url}).encode()

    async def _answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()

    return await asyncio.start_server(_answer, "127.0.0.1", 0)


def _advertise(profile: Path, port: int, target: str) -> Path:
    profile.mkdir(parents=True)
    active = profile / "DevToolsActivePort"
    active.write_text(f"{port}\n{target}", encoding="utf-8")
    return active


@pytest.mark.asyncio
async def test_the_sweep_closes_only_browsers_that_own_their_profile_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A port is only a number after a restart; the browser target id is the proof.

    The orphan answers with the very target its profile recorded and is closed. A port
    now answering with ANOTHER target is not ours to close, and a port nobody answers
    on is a browser already gone — both leave only a stale file behind, removed.
    """
    target = "/devtools/browser/orphan-guid"
    orphan = await _serve_version(f"ws://127.0.0.1:1{target}")
    stranger = await _serve_version("ws://127.0.0.1:1/devtools/browser/someone-else")
    orphan_port = orphan.sockets[0].getsockname()[1]
    stranger_port = stranger.sockets[0].getsockname()[1]
    files = [
        _advertise(tmp_path / "acct-orphan", orphan_port, target),
        _advertise(tmp_path / "acct-reused-port", stranger_port, target),
        _advertise(tmp_path / "acct-gone", 1, target),
    ]
    (tmp_path / "acct-never-opened").mkdir()
    closed_urls: list[str] = []

    async def _record(ws_url: str) -> None:
        closed_urls.append(ws_url)

    monkeypatch.setattr(_lifetime, "_close_browser", _record)
    try:
        closed = await close_orphaned_browsers(tmp_path)
    finally:
        for server in (orphan, stranger):
            server.close()
            await server.wait_closed()

    assert closed == 1
    assert closed_urls == [f"ws://127.0.0.1:1{target}"]
    assert not any(active.exists() for active in files)


@pytest.mark.asyncio
async def test_a_missing_profile_root_is_nothing_to_sweep(tmp_path: Path) -> None:
    assert await close_orphaned_browsers(tmp_path / "web_profiles") == 0


@pytest.mark.asyncio
async def test_an_orphan_is_asked_to_close_over_its_own_devtools_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = RecordingSession()
    connected: list[str] = []

    class _Cdp:
        @staticmethod
        async def connect(ws_url: str) -> RecordingSession:
            connected.append(ws_url)
            return session

    monkeypatch.setattr(_lifetime, "CdpSession", _Cdp)

    await _lifetime._close_browser("ws://127.0.0.1:9/devtools/browser/x")

    assert connected == ["ws://127.0.0.1:9/devtools/browser/x"]
    assert session.methods == ["Browser.close"]
    assert session.closed
