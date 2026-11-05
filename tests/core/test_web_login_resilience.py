"""Web login must not lose an account to a slow page, a CDP error reply or a stalled target.

CDP error replies are failures (they used to be delivered as successes), the handshake is
bounded, a page that was slow to dress gets a second chance and is never run undressed, and
the password field is waited for, then verified, before Enter is pressed.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any

import pytest

from core.web_login import _cdp, _page, _targets
from core.web_login._cdp import CdpError, CdpSession
from core.web_login._passkey import PASSKEY_HOOK
from core.web_login._targets import TargetDriver
from core.web_login.fingerprint import fingerprint_for
from tests.core.test_web_login_targets import _RESUME, _Session, attached
from tests.core.web_login_helpers import RecordingSession, window_for

_SWITCHING = (
    b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
)
_FINGERPRINT = fingerprint_for("acct-1", "DE")


def _frame(text: str) -> bytes:
    payload = text.encode("utf-8")
    return bytes([0x81, len(payload)]) + payload


async def _read_command(reader: asyncio.StreamReader) -> dict[str, Any]:
    head = await reader.readexactly(2)
    mask = await reader.readexactly(4)
    payload = await reader.readexactly(head[1] & 0x7F)
    return json.loads(bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))


# ------------------------------------------------------------------------------------ cdp


@pytest.mark.asyncio
async def test_an_error_reply_is_a_cdp_error_not_a_success() -> None:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(_SWITCHING)
            command = await _read_command(reader)
            error = {"code": -32601, "message": "'Emulation.nope' wasn't found"}
            writer.write(_frame(json.dumps({"id": command["id"], "error": error})))
            await writer.drain()
            await reader.read()
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()

    server = await asyncio.start_server(handle, host="127.0.0.1", port=0)
    port = int(server.sockets[0].getsockname()[1])
    try:
        session = await CdpSession.connect(f"ws://127.0.0.1:{port}/devtools/browser/X")
        with pytest.raises(CdpError, match="-32601"):
            await asyncio.wait_for(session.send_command("Emulation.nope"), 5.0)
        await session.aclose()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_a_browser_that_accepts_and_then_says_nothing_is_a_bounded_cdp_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_cdp, "_HANDSHAKE_TIMEOUT", 0.1)

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read()  # never answers the upgrade
        writer.close()

    server = await asyncio.start_server(handle, host="127.0.0.1", port=0)
    port = int(server.sockets[0].getsockname()[1])
    try:
        with pytest.raises(CdpError):
            await asyncio.wait_for(CdpSession.connect(f"ws://127.0.0.1:{port}/x"), 5.0)
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_a_connect_that_never_completes_is_a_bounded_cdp_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_cdp, "_CONNECT_TIMEOUT", 0.05)

    async def _hang(*_a: object, **_k: object) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(asyncio, "open_connection", _hang)

    with pytest.raises(CdpError):
        await asyncio.wait_for(CdpSession.connect("ws://127.0.0.1:1/x"), 5.0)


@pytest.mark.asyncio
async def test_wait_closed_returns_once_the_pump_ends() -> None:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(_SWITCHING)
        await writer.drain()
        writer.close()  # the browser drops the socket

    server = await asyncio.start_server(handle, host="127.0.0.1", port=0)
    port = int(server.sockets[0].getsockname()[1])
    try:
        session = await CdpSession.connect(f"ws://127.0.0.1:{port}/x")
        await asyncio.wait_for(session.wait_closed(), 5.0)
        assert session.closed
        await session.aclose()
    finally:
        server.close()
        await server.wait_closed()


# ---------------------------------------------------------------------------------- page


@pytest.mark.asyncio
async def test_the_field_is_clicked_only_once_it_stopped_moving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_page, "_SETTLE_READ_GAP_SECONDS", 0)
    monkeypatch.setattr(_page, "_CLICK_SETTLE_SECONDS", 0)
    positions = iter(['{"x":300,"y":50}', '{"x":150,"y":120}', '{"x":100,"y":200}'])

    class Sliding(RecordingSession):
        async def send_command(self, method: str, params: Any = None, **kw: Any) -> Any:
            if method == "Runtime.evaluate" and params["expression"] == _page._INPUT_RECT_EXPR:
                self.commands.append((method, params, kw.get("session_id")))
                value = next(positions, '{"x":100,"y":200}')
                return {"result": {"result": {"type": "string", "value": value}}}
            return await super().send_command(method, params, **kw)

    session = Sliding()

    assert await _page.type_2fa_password(window_for(session), "pw") is True

    clicks = [
        (p["x"], p["y"])
        for m, p, _s in session.commands
        if m == "Input.dispatchMouseEvent" and p["type"] == "mousePressed"
    ]
    assert clicks == [(100, 200)]


@pytest.mark.asyncio
async def test_a_field_that_does_not_take_the_text_is_never_submitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_page, "_SETTLE_READ_GAP_SECONDS", 0)
    monkeypatch.setattr(_page, "_CLICK_SETTLE_SECONDS", 0)
    session = RecordingSession(
        {_page._INPUT_RECT_EXPR: '{"x":1,"y":2}', _page._FIELD_LENGTH_EXPR: "0"}
    )

    # RecordingSession answers strings; the length probe needs a number.
    original = session.send_command

    async def send(method: str, params: Any = None, **kw: Any) -> Any:
        if method == "Runtime.evaluate" and params["expression"] == _page._FIELD_LENGTH_EXPR:
            session.commands.append((method, params, kw.get("session_id")))
            return {"result": {"result": {"type": "number", "value": 0}}}
        return await original(method, params, **kw)

    session.send_command = send  # ty: ignore[invalid-assignment]

    assert await _page.type_2fa_password(window_for(session), "pw") is False

    keys = [p for m, p, _s in session.commands if m == "Input.dispatchKeyEvent"]
    assert not [k for k in keys if k.get("key") == "Enter"]  # nothing was submitted
    clicks = [p for m, p, _s in session.commands if m == "Input.dispatchMouseEvent"]
    assert len(clicks) == 2 * _page._TYPE_ATTEMPTS  # it tried again before giving up


@pytest.mark.asyncio
async def test_password_outcome_reads_error_checking_or_idle() -> None:
    for value, expected in (("error", "error"), ("checking", "checking"), ("x", "idle")):
        session = RecordingSession({_page._SUBMIT_OUTCOME_EXPR: value})
        assert await _page.password_outcome(window_for(session)) == expected


# ------------------------------------------------------------------------------- targets


class _Flaky(_Session):
    """A session whose named method fails (hangs) for its first ``times`` calls."""

    def __init__(self, *args: Any, flaky: str, times: int, hang: bool = False, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self._flaky = flaky
        self._times = times
        self._hangs = hang

    async def send_command(self, method: str, params: Any = None, *, session_id: Any = None) -> Any:
        if method == self._flaky and self._times > 0:
            self._times -= 1
            if self._hangs:
                await asyncio.Event().wait()
            msg = f"{method} refused"
            raise CdpError(msg)
        return await super().send_command(method, params, session_id=session_id)


@pytest.mark.asyncio
async def test_a_page_that_was_slow_to_dress_is_dressed_on_the_retry_and_resumed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_targets, "_DRESS_TIMEOUT", 0.05)
    monkeypatch.setattr(_targets, "_DRESS_RETRY_TIMEOUT", 1.0)
    session = _Flaky(
        events=[attached("P1", "page")], flaky="Emulation.setTimezoneOverride", times=1, hang=True
    )
    driver = TargetDriver(session, _FINGERPRINT, page_scripts=("window.__x=1",))  # ty: ignore[invalid-argument-type]

    assert await driver.first_page_session() == "P1"

    assert session.sent(_RESUME)[0][1] == "P1"
    # Resumed, not repeated: the document-start script went in exactly once.
    scripts = session.sent("Page.addScriptToEvaluateOnNewDocument")
    assert len([p for p, _s in scripts if p["source"] == "window.__x=1"]) == 1


@pytest.mark.asyncio
async def test_a_page_that_cannot_be_dressed_is_closed_and_never_resumed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_targets, "_DRESS_TIMEOUT", 0.05)
    monkeypatch.setattr(_targets, "_DRESS_RETRY_TIMEOUT", 0.05)
    monkeypatch.setattr(_targets, "_FIRST_PAGE_TIMEOUT", 0.3)
    session = _Flaky(
        events=[attached("P1", "page")], flaky="Emulation.setTimezoneOverride", times=99, hang=True
    )

    with pytest.raises(CdpError):
        await TargetDriver(session, _FINGERPRINT).first_page_session()  # ty: ignore[invalid-argument-type]

    assert session.sent(_RESUME) == []  # an undressed page never runs
    assert session.sent("Target.closeTarget") == [({"targetId": "P1"}, None)]


@pytest.mark.asyncio
async def test_a_worker_whose_identity_script_threw_counts_as_undressed() -> None:
    class Throwing(_Session):
        async def send_command(
            self, method: str, params: Any = None, *, session_id: Any = None
        ) -> Any:
            await super().send_command(method, params, session_id=session_id)
            if method == "Runtime.evaluate":
                return {"result": {"exceptionDetails": {"text": "boom"}}}
            return {"result": {}}

    session = Throwing(events=[attached("W1", "worker")])

    with pytest.raises(CdpError):
        await TargetDriver(session, _FINGERPRINT).first_page_session()  # ty: ignore[invalid-argument-type]

    assert session.sent("Browser.close")
    assert session.sent(_RESUME) == []


# ---------------------------------------------------------------------------------- hook


def test_the_passkey_hook_skips_requests_that_were_aborted() -> None:
    assert "options.signal.aborted" in PASSKEY_HOOK  # rejected up front, never queued
    assert "waiting.has(next.id)" in PASSKEY_HOOK  # take() never hands out an aborted one
