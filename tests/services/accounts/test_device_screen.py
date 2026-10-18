"""A phone's web screen: one viewer per phone, the controls exclusive with warming.

``open_screen`` and ``running_serial`` are fakes on ``screen``'s own names; the
manual/activity registry is the real one in ``mobile_emulator``.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.emulator import EmulatorError
from services.accounts import mobile_emulator as emulator_module
from services.accounts.mobile_emulator import (
    EmulatorBusyError,
    EmulatorRefusedError,
    claim_manual,
    open_account_mobile,
    release_manual,
    run_mobile_activity,
)
from services.device_farm import ScreenCommandError, ScreenSupersededError, parse_command, screen
from services.device_farm import screen_session as open_session
from tests.services.accounts.test_mobile_emulator import _enabled  # noqa: F401

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from schemas.device_farm import ScreenCommand
    from schemas.emulator import EmulatorRefusalCode

_PHONE = "telebuba-acc"


class _Stream:
    def __init__(self, *, control: bool) -> None:
        self.controllable = control
        self.sent: list[ScreenCommand] = []
        self.fail_send = False

    async def read_video(self) -> bytes:
        return b"frame"

    async def read_clipboard(self) -> str | None:
        return "clip"

    async def send(self, command: ScreenCommand) -> None:
        if self.fail_send:
            raise ConnectionError
        self.sent.append(command)


class _Screens:
    def __init__(self) -> None:
        self.opened: list[tuple[str, bool]] = []
        self.closed = 0
        self.fail: EmulatorRefusalCode | None = None
        self.streams: list[_Stream] = []

    @asynccontextmanager
    async def open_screen(self, serial: str, *, control: bool) -> AsyncIterator[_Stream]:
        if self.fail is not None:
            raise EmulatorError(self.fail)
        self.opened.append((serial, control))
        stream = _Stream(control=control)
        self.streams.append(stream)
        try:
            yield stream
        finally:
            self.closed += 1


@pytest.fixture
def screens(monkeypatch: pytest.MonkeyPatch) -> _Screens:
    fake = _Screens()

    async def running_serial(account_id: str) -> str:
        assert account_id == "acc"
        return "127.0.0.1:5601"

    monkeypatch.setattr(screen, "open_screen", fake.open_screen)
    monkeypatch.setattr(screen, "running_serial", running_serial)
    emulator_module._ACTIVE.clear()
    emulator_module._MANUAL.clear()
    screen._VIEWERS.clear()
    return fake


@pytest.mark.asyncio
async def test_a_view_takes_no_controls(screens: _Screens) -> None:
    async with open_session(_PHONE, "view") as session:
        assert not session.controllable
        assert await session.read_video() == b"frame"
        assert emulator_module.phone_activity("acc") is None

    assert screens.opened == [("127.0.0.1:5601", False)]
    assert screens.closed == 1
    assert screen._VIEWERS == {}


@pytest.mark.asyncio
async def test_control_holds_the_phone_until_the_viewer_leaves(screens: _Screens) -> None:
    async with open_session(_PHONE, "control") as session:
        assert session.controllable
        assert emulator_module.phone_activity("acc") == "manual"
        await session.send(parse_command('{"type": "key", "action": "down", "keycode": 4}'))
        assert await session.read_clipboard() == "clip"

    assert emulator_module.phone_activity("acc") is None
    assert screens.opened == [("127.0.0.1:5601", True)]
    assert [c.type for c in screens.streams[0].sent] == ["key"]


@pytest.mark.asyncio
async def test_a_failed_open_still_releases_the_controls(screens: _Screens) -> None:
    screens.fail = "emulator_scrcpy_missing"

    with pytest.raises(EmulatorRefusedError) as refused:
        async with open_session(_PHONE, "control"):
            pytest.fail("no screen without the server")

    assert refused.value.code == "emulator_scrcpy_missing"
    assert emulator_module.phone_activity("acc") is None
    assert screen._VIEWERS == {}


@pytest.mark.asyncio
async def test_a_command_the_phone_cannot_take_is_a_failed_command(screens: _Screens) -> None:
    async with open_session(_PHONE, "control") as session:
        screens.streams[0].fail_send = True
        with pytest.raises(EmulatorRefusedError) as refused:
            await session.send(parse_command('{"type": "clipboard_get"}'))

    assert refused.value.code == "emulator_command_failed"


@pytest.mark.asyncio
async def test_control_is_refused_while_warming_drives_the_phone(screens: _Screens) -> None:
    emulator_module._ACTIVE.add("acc")

    with pytest.raises(EmulatorBusyError):
        async with open_session(_PHONE, "control"):
            pytest.fail("the warming stint holds the phone")
    async with open_session(_PHONE, "view") as session:
        assert not session.controllable  # watching a stint is fine

    assert screens.opened == [("127.0.0.1:5601", False)]


@pytest.mark.asyncio
async def test_a_refused_control_leaves_the_current_viewer_watching(screens: _Screens) -> None:
    emulator_module._ACTIVE.add("acc")
    watching = asyncio.Event()
    leave = asyncio.Event()

    async def watcher() -> bool:
        async with open_session(_PHONE, "view") as session:
            watching.set()
            superseded = asyncio.create_task(session.wait_superseded())
            await leave.wait()
            await asyncio.sleep(0)
            told = superseded.done()
            superseded.cancel()
            return told

    task = asyncio.create_task(watcher())
    await watching.wait()
    with pytest.raises(EmulatorBusyError):
        async with open_session(_PHONE, "control"):
            pytest.fail("the warming stint holds the phone")
    leave.set()

    assert await task is False, "the watcher was never told to leave"
    assert screens.closed == 1


@pytest.mark.asyncio
async def test_warming_is_refused_while_an_operator_has_the_controls(
    screens: _Screens,  # noqa: ARG001 - isolates the registries
) -> None:
    claim_manual("acc")
    try:
        with pytest.raises(EmulatorBusyError):
            await run_mobile_activity("acc", 60)
        with pytest.raises(EmulatorBusyError):
            claim_manual("acc")
    finally:
        release_manual("acc")

    assert emulator_module.phone_activity("acc") is None


@pytest.mark.asyncio
async def test_a_new_viewer_supersedes_the_current_one(screens: _Screens) -> None:
    first_in = asyncio.Event()
    first_told = asyncio.Event()

    async def first() -> None:
        async with open_session(_PHONE, "control") as session:
            first_in.set()
            await session.wait_superseded()
            first_told.set()
            await asyncio.sleep(0.05)  # still letting go when the second one waits

    task = asyncio.create_task(first())
    await first_in.wait()
    async with open_session(_PHONE, "control") as second:
        assert first_told.is_set()
        assert screens.closed == 1, "the old server is gone before the new one starts"
        assert second.controllable
    await task

    assert len(screens.opened) == 2
    assert screen._VIEWERS == {}
    assert emulator_module.phone_activity("acc") is None


@pytest.mark.asyncio
async def test_the_screen_is_refused_while_switched_off(
    screens: _Screens, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "enabled", False)

    with pytest.raises(EmulatorRefusedError) as refused:
        async with open_session(_PHONE, "view"):
            pytest.fail("switched off")

    assert refused.value.code == "mobile_emulator_disabled"
    assert screens.opened == []


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"type": "start_app", "name": "com.evil"}',
        '{"type": "touch", "action": "down", "x": -1, "y": 0, "width": 1, "height": 1}',
        '{"type": "text", "text": ""}',
        '{"type": "key", "action": "down", "keycode": 9999}',
    ],
)
def test_anything_but_the_screen_commands_is_refused(text: str) -> None:
    with pytest.raises(ScreenCommandError):
        parse_command(text)


def test_the_screen_commands_parse() -> None:
    touch = parse_command(
        '{"type": "touch", "action": "move", "x": 5, "y": 6, "width": 720, "height": 1280}'
    )

    assert touch.type == "touch"
    assert parse_command('{"type": "text", "text": "hi"}').type == "text"


@pytest.mark.asyncio
async def test_a_viewer_superseded_while_waiting_never_starts_a_server(screens: _Screens) -> None:
    first_in = asyncio.Event()

    async def first() -> None:
        async with open_session(_PHONE, "control") as session:
            first_in.set()
            await session.wait_superseded()
            await asyncio.sleep(0.05)  # still letting go while two more queue up

    async def second() -> None:
        async with open_session(_PHONE, "control"):
            pytest.fail("a third viewer took the phone first")

    first_task = asyncio.create_task(first())
    await first_in.wait()
    second_task = asyncio.create_task(second())
    await asyncio.sleep(0.01)
    async with open_session(_PHONE, "control") as third:
        assert third.controllable
    await first_task
    with pytest.raises(ScreenSupersededError):
        await second_task

    assert len(screens.opened) == 2, "the waiting viewer started no server"
    assert emulator_module.phone_activity("acc") is None


@pytest.mark.asyncio
async def test_an_open_is_refused_while_an_operator_has_the_controls(
    screens: _Screens,  # noqa: ARG001 - isolates the registries
) -> None:
    claim_manual("acc")
    try:
        with pytest.raises(EmulatorBusyError):
            await open_account_mobile("acc")
    finally:
        release_manual("acc")


@pytest.mark.asyncio
async def test_a_viewer_superseded_while_opening_is_never_shown(
    screens: _Screens, monkeypatch: pytest.MonkeyPatch
) -> None:
    opening = asyncio.Event()
    release = asyncio.Event()
    real_open = screens.open_screen

    @asynccontextmanager
    async def slow_open(serial: str, *, control: bool) -> AsyncIterator[_Stream]:
        if not screens.opened:
            opening.set()
            await release.wait()  # a cold phone: the first open takes its time
        async with real_open(serial, control=control) as stream:
            yield stream

    monkeypatch.setattr(screen, "open_screen", slow_open)

    async def first() -> None:
        async with open_session(_PHONE, "control"):
            pytest.fail("superseded before it was ever shown")

    first_task = asyncio.create_task(first())
    await opening.wait()
    second = asyncio.create_task(_open_and_leave())
    await asyncio.sleep(0.01)
    release.set()
    with pytest.raises(ScreenSupersededError):
        await first_task
    assert await second is True

    assert screens.closed == 2
    assert emulator_module.phone_activity("acc") is None


async def _open_and_leave() -> bool:
    async with open_session(_PHONE, "control") as session:
        return session.controllable
