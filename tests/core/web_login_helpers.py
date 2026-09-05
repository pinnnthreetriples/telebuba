"""Shared fakes for the web-login browser tests.

A real browser cannot run in CI, so the CDP socket and the browser process are
recorded fakes. Both the launcher tests and the page-primitive tests drive the same
two doubles, so they live here rather than being copied into each file.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.web_login import browser
from core.web_login._cdp import CdpError
from core.web_login._targets import TargetDriver
from core.web_login.browser import WebWindow
from core.web_login.fingerprint import fingerprint_for

if TYPE_CHECKING:
    import pytest

PAGE = "PAGE-1"
FINGERPRINT = fingerprint_for("acct-1", "DE")


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
        *,
        fail_on: str | None = None,
        fail_close: bool = False,
    ) -> None:
        self.commands: list[tuple[str, dict[str, object], str | None]] = []
        self._evaluate_values = evaluate_values or {}
        self._events = list(events or [])
        self._fail_on = fail_on
        self._fail_close = fail_close
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
        if method == self._fail_on:
            msg = f"{method} refused"
            raise CdpError(msg)
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
        if self._fail_close:
            # A pump task that ended with an exception outside the four types
            # ``_route`` catches re-raises out of this await.
            msg = "DevTools session ended badly"
            raise CdpError(msg)

    @property
    def methods(self) -> list[str]:
        return [method for method, _params, _session in self.commands]


class FakeProc:
    """A browser process double: records terminate/kill/wait, never really spawns."""

    def __init__(self, recorder: dict[str, Any]) -> None:
        self._recorder = recorder
        self.returncode: int | None = None

    def terminate(self) -> None:
        self._recorder["terminated"] = True

    def kill(self) -> None:
        self._recorder["killed"] = True
        self.returncode = 1

    async def wait(self) -> int:
        self._recorder["waited"] = True
        return 0


def window_for(session: RecordingSession) -> WebWindow:
    """A window around ``session`` for the primitives that drive an open page."""
    return WebWindow(
        session=session,  # ty: ignore[invalid-argument-type]
        driver=TargetDriver(session, FINGERPRINT),  # ty: ignore[invalid-argument-type]
        page=PAGE,
        process=FakeProc({}),  # ty: ignore[invalid-argument-type]
    )


class FakeCdpFactory:
    """Hands out the scripted session, or raises when the test wants a failed attach."""

    def __init__(self, session: RecordingSession, recorder: dict[str, Any]) -> None:
        self._session = session
        self._recorder = recorder

    async def connect(self, ws_url: str) -> RecordingSession:
        self._recorder["ws_url"] = ws_url
        return self._session


def wire_launch(
    monkeypatch: pytest.MonkeyPatch,
    recorder: dict[str, Any],
    session: RecordingSession,
    *,
    proc: FakeProc | None = None,
) -> None:
    async def _fake_exec(program: str, *args: str, **kwargs: object) -> FakeProc:
        recorder["exec"] = (program, args, kwargs)
        return proc if proc is not None else FakeProc(recorder)

    async def _fake_ws(_debug_port: int, _process: object, _profile: Path) -> str:
        return "ws://127.0.0.1:5555/devtools/browser/ABC"

    monkeypatch.setattr(browser, "find_browser", lambda: Path(r"C:\fake\chrome.exe"))
    monkeypatch.setattr(browser, "_free_port", lambda: 5555)
    monkeypatch.setattr(browser, "_browser_ws", _fake_ws)
    monkeypatch.setattr(browser, "CdpSession", FakeCdpFactory(session, recorder))
    monkeypatch.setattr(browser.asyncio, "create_subprocess_exec", _fake_exec)
    # Process-global, so a leaked reservation from one test would starve the next.
    monkeypatch.setattr(browser, "_ports_in_flight", set())
