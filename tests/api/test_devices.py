"""Device farm routes: the phone list and actions, and the screen WebSocket's contract.

The service is faked on ``services.device_farm``'s names; the handshake's own guards
(session cookie, Origin) run for real.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import httpx
import pytest

from api.v1 import devices as devices_api
from schemas.auth import UserRead
from schemas.device_farm import Phone, PhoneActionResult, PhoneApp, PhoneList
from services import device_farm
from services.accounts.mobile_emulator import EmulatorBusyError, EmulatorRefusedError
from tests.api._asgi_ws import AsgiWebSocket, Closed

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

    from schemas.device_farm import ScreenCommand, ScreenMode
    from schemas.emulator import EmulatorRefusalCode

_ORIGIN = {"origin": "http://testserver"}
_URL = "/api/v1/devices/telebuba-acc/screen"


class _Session:
    def __init__(self, *, controllable: bool, frames: list[bytes]) -> None:
        self.controllable = controllable
        self.frames = list(frames)
        self.sent: list[ScreenCommand] = []
        self.clipboard: list[str] = []
        self.superseded = asyncio.Event()
        self.hold = asyncio.Event()

    async def read_video(self) -> bytes:
        if self.frames:
            return self.frames.pop(0)
        await self.hold.wait()
        return b""

    async def read_clipboard(self) -> str | None:
        if self.clipboard:
            return self.clipboard.pop(0)
        await asyncio.Event().wait()
        return None

    async def send(self, command: ScreenCommand) -> None:
        self.sent.append(command)
        if command.type == "clipboard_get":
            self.hold.set()

    async def wait_superseded(self) -> None:
        await self.superseded.wait()


class _Farm:
    def __init__(self) -> None:
        self.session = _Session(controllable=True, frames=[b"\x00\x01", b"\x02"])
        self.refuse: EmulatorRefusedError | None = None
        self.modes: list[ScreenMode] = []
        self.left = 0

    @asynccontextmanager
    async def screen_session(self, phone_id: str, mode: ScreenMode) -> AsyncIterator[_Session]:
        assert phone_id == "telebuba-acc"
        self.modes.append(mode)
        if self.refuse is not None:
            raise self.refuse
        try:
            yield self.session
        finally:
            self.left += 1


@pytest.fixture
def farm(monkeypatch: pytest.MonkeyPatch) -> _Farm:
    fake = _Farm()
    monkeypatch.setattr(device_farm, "screen_session", fake.screen_session)

    async def signed_in(_websocket: object) -> UserRead:
        return UserRead(id="test-admin", username="admin", role="admin")

    monkeypatch.setattr(devices_api, "get_websocket_user", signed_in)
    return fake


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.mark.asyncio
async def test_the_screen_relays_video_and_takes_commands(app: FastAPI, farm: _Farm) -> None:
    async with AsgiWebSocket(app, f"{_URL}?mode=control", _ORIGIN) as socket:
        assert await socket.receive_json() == {"type": "ready", "controllable": True}
        assert await socket.receive() == b"\x00\x01"
        assert await socket.receive() == b"\x02"
        await socket.send_text(json.dumps({"type": "key", "action": "down", "keycode": 3}))
        await socket.send_text(json.dumps({"type": "clipboard_get"}))  # then the video ends
        with pytest.raises(Closed) as closed:
            await socket.receive()

    assert (closed.value.code, closed.value.reason) == (1000, "screen_ended")
    assert farm.modes == ["control"]
    assert [command.type for command in farm.session.sent] == ["key", "clipboard_get"]
    assert farm.left == 1


@pytest.mark.asyncio
async def test_the_phone_clipboard_comes_back_as_text(app: FastAPI, farm: _Farm) -> None:
    farm.session.frames = []
    farm.session.clipboard = ["copied"]

    async with AsgiWebSocket(app, f"{_URL}?mode=control", _ORIGIN) as socket:
        await socket.receive_json()
        assert await socket.receive_json() == {"type": "clipboard", "text": "copied"}

    assert farm.left == 1, "a viewer that just leaves still ends the session"


@pytest.mark.asyncio
async def test_view_is_the_default_and_a_command_there_closes_it(app: FastAPI, farm: _Farm) -> None:
    farm.session = _Session(controllable=False, frames=[])

    async with AsgiWebSocket(app, _URL, _ORIGIN) as socket:
        assert (await socket.receive_json())["controllable"] is False
        await socket.send_text(json.dumps({"type": "key", "action": "down", "keycode": 3}))
        with pytest.raises(Closed) as closed:
            await socket.receive()

    assert (closed.value.code, closed.value.reason) == (1008, "screen_bad_command")
    assert farm.modes == ["view"]
    assert farm.session.sent == []


@pytest.mark.parametrize(
    "frame",
    ['{"type": "start_app", "name": "x"}', "garbage", b"\x02raw scrcpy bytes"],
)
@pytest.mark.asyncio
async def test_a_frame_that_is_no_command_closes_the_screen(
    app: FastAPI, farm: _Farm, frame: str | bytes
) -> None:
    farm.session.frames = []

    async with AsgiWebSocket(app, f"{_URL}?mode=control", _ORIGIN) as socket:
        await socket.receive_json()
        if isinstance(frame, bytes):
            await socket.send_bytes(frame)
        else:
            await socket.send_text(frame)
        with pytest.raises(Closed) as closed:
            await socket.receive()

    assert (closed.value.code, closed.value.reason) == (1008, "screen_bad_command")
    assert farm.session.sent == []


@pytest.mark.asyncio
async def test_a_superseded_viewer_is_told_why(app: FastAPI, farm: _Farm) -> None:
    farm.session.frames = []
    farm.session.superseded.set()

    async with AsgiWebSocket(app, _URL, _ORIGIN) as socket:
        await socket.receive_json()
        with pytest.raises(Closed) as closed:
            await socket.receive()

    assert (closed.value.code, closed.value.reason) == (1000, "screen_superseded")


@pytest.mark.parametrize("code", ["emulator_not_running", "emulator_busy"])
@pytest.mark.asyncio
async def test_a_refused_screen_closes_with_its_code(
    app: FastAPI, farm: _Farm, code: EmulatorRefusalCode
) -> None:
    farm.refuse = EmulatorBusyError() if code == "emulator_busy" else EmulatorRefusedError(code)

    async with AsgiWebSocket(app, _URL, _ORIGIN) as socket:
        with pytest.raises(Closed) as closed:
            await socket.receive()

    assert (closed.value.code, closed.value.reason) == (4000, code)


@pytest.mark.asyncio
async def test_a_handshake_without_a_session_is_refused(app: FastAPI) -> None:
    with pytest.raises(Closed) as refused:
        async with AsgiWebSocket(app, _URL, _ORIGIN):
            pytest.fail("no screen without a session")

    assert refused.value.code == 1008


@pytest.mark.parametrize(
    "headers",
    [{}, {"origin": "http://evil.example"}, {"origin": "null"}],
)
@pytest.mark.asyncio
async def test_a_handshake_from_another_origin_is_refused(
    app: FastAPI, farm: _Farm, headers: dict[str, str]
) -> None:
    with pytest.raises(Closed) as refused:
        async with AsgiWebSocket(app, _URL, headers):
            pytest.fail("cross-site WebSocket hijacking")

    assert refused.value.code == 1008
    assert farm.modes == []


@pytest.mark.parametrize("url", ["/api/v1/devices/bad%20id/screen", f"{_URL}?mode=admin"])
@pytest.mark.asyncio
async def test_a_bad_phone_id_or_mode_is_refused(app: FastAPI, farm: _Farm, url: str) -> None:
    with pytest.raises(Closed):
        async with AsgiWebSocket(app, url, _ORIGIN):
            pytest.fail("refused before the session")

    assert farm.modes == []


def _phone() -> Phone:
    return Phone(
        phone_id="telebuba-acc",
        created=True,
        status="free",
        maker="Google",
        model_name="Pixel 7",
        device_model="GooglePixel 7",
        system_version="SDK 34",
        app_version="11.0",
        lang_code="es",
        apps=[PhoneApp(platform="telegram", account_id="acc")],
    )


@pytest.mark.asyncio
async def test_the_phone_list_is_served(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def listed() -> PhoneList:
        return PhoneList(enabled=True, phones=[_phone()])

    monkeypatch.setattr(device_farm, "list_phones", listed)

    async with _client(app) as client:
        response = await client.get("/api/v1/devices")

    assert response.status_code == 200
    assert response.json()["phones"][0]["phone_id"] == "telebuba-acc"


@pytest.mark.parametrize("action", ["start", "stop"])
@pytest.mark.asyncio
async def test_a_busy_phone_answers_409(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    async def busy(_phone_id: str) -> PhoneActionResult:
        raise EmulatorBusyError

    monkeypatch.setattr(device_farm, f"{action}_phone", busy)

    async with _client(app) as client:
        response = await client.post(f"/api/v1/devices/telebuba-acc/{action}", headers=_ORIGIN)

    assert response.status_code == 409
    assert response.json()["error"]["message"] == "emulator_busy"


@pytest.mark.asyncio
async def test_a_refused_start_answers_400_with_its_code(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refused(_phone_id: str) -> PhoneActionResult:
        code: EmulatorRefusalCode = "mobile_emulator_disabled"
        raise EmulatorRefusedError(code)

    monkeypatch.setattr(device_farm, "start_phone", refused)

    async with _client(app) as client:
        response = await client.post("/api/v1/devices/telebuba-acc/start", headers=_ORIGIN)

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "mobile_emulator_disabled"


@pytest.mark.asyncio
async def test_a_started_phone_answers_starting(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def started(phone_id: str) -> PhoneActionResult:
        return PhoneActionResult(phone_id=phone_id, status="starting")

    monkeypatch.setattr(device_farm, "start_phone", started)

    async with _client(app) as client:
        response = await client.post("/api/v1/devices/telebuba-acc/start", headers=_ORIGIN)

    assert response.json() == {"phone_id": "telebuba-acc", "status": "starting"}
