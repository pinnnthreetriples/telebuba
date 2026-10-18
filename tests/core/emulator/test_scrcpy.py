"""The web screen's scrcpy-server: started per viewer, gone when the viewer is.

A real loopback TCP server stands in for adb's forwarded port; the adb console and
the ``adb shell`` process are fakes. The server's sockets behave as scrcpy's do: the
video socket answers with one dummy byte, the control one with nothing.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from core.config import settings
from core.emulator import EmulatorError, open_screen
from core.emulator import scrcpy as scrcpy_module
from schemas.device_farm import ScreenKey

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from tests.core.emulator.conftest import Console

_SERIAL = "127.0.0.1:5600"


class _Phone:
    """The forwarded port: what scrcpy-server would accept and send."""

    def __init__(self, *, refuse_first: int = 0, answers: bool = True) -> None:
        self.refuse_first = refuse_first
        self.answers = answers
        self.silent = False
        self.connections = 0
        self.control_bytes = bytearray()
        self.video_payload = b"\x00\x00\x00\x00video"
        self.all_closed = asyncio.Event()
        self._open = 0
        self._control_writer: asyncio.StreamWriter | None = None

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        if self.silent:
            await reader.read()  # holds the line open, never passes the byte
            writer.close()
            return
        if self.connections <= self.refuse_first or not self.answers:
            writer.close()  # adb accepted, but nothing listens on the phone yet
            return
        self._open += 1
        if self._open == 1:
            writer.write(b"\x00" + self.video_payload)  # the dummy byte, then video
            await writer.drain()
        else:
            self._control_writer = writer
            writer.write(b"\x00\x00\x00\x00\x02hi")  # the device's clipboard
            await writer.drain()
        while chunk := await reader.read(1024):
            if self._open >= 2 and writer is self._control_writer:
                self.control_bytes.extend(chunk)
        self._open -= 1
        writer.close()
        if self._open == 0:
            self.all_closed.set()


class _ServerProcess:
    def __init__(self, *, exits_with_sockets: bool, phone: _Phone) -> None:
        self.returncode: int | None = None
        self.killed = False
        self._exits_with_sockets = exits_with_sockets
        self._phone = phone

        self._killed = asyncio.Event()

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self._killed.set()

    async def wait(self) -> int:
        if self._exits_with_sockets:
            await self._phone.all_closed.wait()
            self.returncode = 0
            return 0
        await self._killed.wait()
        return -9


@pytest.fixture
def server_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "scrcpy-server"
    path.write_bytes(b"jar")
    monkeypatch.setattr(settings.emulator, "scrcpy_server_path", path)
    monkeypatch.setattr(settings.emulator, "stream_connect_timeout_seconds", 2.0)
    return path


@pytest_asyncio.fixture
async def phone() -> AsyncIterator[tuple[_Phone, int]]:
    fake = _Phone()
    listener = await asyncio.start_server(fake.handle, "127.0.0.1", 0)
    port = listener.sockets[0].getsockname()[1]
    yield fake, port
    listener.close()


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch, phone: tuple[_Phone, int]) -> list[list[str]]:
    calls: list[list[str]] = []

    async def spawn(*argv: str, **_streams: object) -> _ServerProcess:
        calls.append(list(argv))
        return _ServerProcess(exits_with_sockets=True, phone=phone[0])

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return calls


@pytest.fixture
def adb(console: Console, monkeypatch: pytest.MonkeyPatch, phone: tuple[_Phone, int]) -> Console:
    console.answer = lambda argv: f"{phone[1]}\n" if "tcp:0" in argv else ""
    monkeypatch.setattr("core.emulator.scrcpy.run_command", console)
    return console


def _adb_steps(console: Console) -> list[list[str]]:
    return [call[3:] for call in console.calls if call[1:3] == ["-s", _SERIAL]]


@pytest.mark.usefixtures("server_file")
@pytest.mark.asyncio
async def test_a_view_streams_video_and_leaves_nothing_running(
    adb: Console, spawned: list[list[str]], phone: tuple[_Phone, int]
) -> None:
    async with open_screen(_SERIAL, control=False) as stream:
        assert not stream.controllable
        assert await stream.read_video() == phone[0].video_payload

    steps = _adb_steps(adb)
    assert steps[0][0] == "push"
    assert steps[1][:2] == ["forward", "tcp:0"]
    assert steps[1][2].startswith("localabstract:scrcpy_")
    assert steps[-1] == ["forward", "--remove", f"tcp:{phone[1]}"]
    assert not any("pkill" in step for step in steps), "the server left with its socket"
    shell = spawned[0][spawned[0].index("shell") + 1 :]
    assert "control=false" in shell
    assert "audio=false" in shell
    assert not any(arg.startswith("raw_stream") for arg in shell), "frame headers stay on"
    assert shell[4] == settings.emulator.scrcpy_server_version


@pytest.mark.usefixtures("server_file", "adb", "spawned")
@pytest.mark.asyncio
async def test_control_writes_commands_and_reads_the_clipboard(phone: tuple[_Phone, int]) -> None:
    async with open_screen(_SERIAL, control=True) as stream:
        assert stream.controllable
        await stream.send(ScreenKey(type="key", action="down", keycode=3))
        assert await stream.read_clipboard() == "hi"
        await asyncio.sleep(0.05)

    assert bytes(phone[0].control_bytes) == bytes([0, 0, 0, 0, 0, 3]) + bytes(8)


@pytest.mark.usefixtures("server_file", "adb", "spawned")
@pytest.mark.asyncio
async def test_a_connection_counts_only_once_the_dummy_byte_arrives(
    phone: tuple[_Phone, int],
) -> None:
    phone[0].refuse_first = 3

    async with open_screen(_SERIAL, control=False) as stream:
        assert await stream.read_video() == phone[0].video_payload

    assert phone[0].connections == 4


@pytest.mark.usefixtures("server_file", "adb", "spawned")
@pytest.mark.asyncio
async def test_a_server_that_never_answers_times_out_and_is_cleaned_up(
    phone: tuple[_Phone, int], monkeypatch: pytest.MonkeyPatch, adb: Console
) -> None:
    phone[0].answers = False
    phone[0].all_closed.set()  # nothing is open: the fake server exits at once
    monkeypatch.setattr(settings.emulator, "stream_connect_timeout_seconds", 0.3)

    with pytest.raises(EmulatorError) as refused:
        async with open_screen(_SERIAL, control=False):
            pytest.fail("no screen without the server's answer")

    assert refused.value.code == "emulator_screen_timeout"
    assert _adb_steps(adb)[-1][:2] == ["forward", "--remove"]


@pytest.mark.usefixtures("server_file", "adb")
@pytest.mark.asyncio
async def test_a_server_outliving_its_sockets_is_killed_by_its_scid(
    monkeypatch: pytest.MonkeyPatch, phone: tuple[_Phone, int], adb: Console
) -> None:
    stuck = _ServerProcess(exits_with_sockets=False, phone=phone[0])

    async def spawn(*_argv: str, **_streams: object) -> _ServerProcess:
        return stuck

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(scrcpy_module, "_EXIT_GRACE_SECONDS", 0.05)

    async with open_screen(_SERIAL, control=False):
        pass

    pkill = next(step for step in _adb_steps(adb) if "pkill" in step)
    assert pkill[:3] == ["shell", "pkill", "-f"]
    assert pkill[3].startswith("scid=")
    assert stuck.killed


@pytest.mark.usefixtures("server_file", "spawned")
@pytest.mark.asyncio
async def test_a_cancelled_viewer_still_stops_the_server(
    phone: tuple[_Phone, int], adb: Console
) -> None:
    entered = asyncio.Event()

    async def watch() -> None:
        async with open_screen(_SERIAL, control=True):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(watch())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The cancellation reached the caller only once the cleanup was complete.
    assert _adb_steps(adb)[-1][:2] == ["forward", "--remove"]

    async with asyncio.timeout(2):
        await phone[0].all_closed.wait()


@pytest.mark.usefixtures("adb", "spawned")
@pytest.mark.asyncio
async def test_a_missing_server_file_is_refused_before_adb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adb: Console
) -> None:
    monkeypatch.setattr(settings.emulator, "scrcpy_server_path", tmp_path / "absent")

    with pytest.raises(EmulatorError) as refused:
        async with open_screen(_SERIAL, control=False):
            pytest.fail("no screen without the server file")

    assert refused.value.code == "emulator_scrcpy_missing"
    assert adb.calls == []


@pytest.mark.usefixtures("server_file", "spawned")
@pytest.mark.asyncio
async def test_a_forward_without_a_port_is_a_failed_command(adb: Console) -> None:
    adb.answer = lambda _argv: "error\n"

    with pytest.raises(EmulatorError) as refused:
        async with open_screen(_SERIAL, control=False):
            pytest.fail("no screen without a forwarded port")

    assert refused.value.code == "emulator_command_failed"


@pytest.mark.usefixtures("server_file", "adb")
@pytest.mark.asyncio
async def test_a_missing_adb_for_the_server_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    async def missing(*_argv: str, **_streams: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", missing)

    with pytest.raises(EmulatorError) as refused:
        async with open_screen(_SERIAL, control=False):
            pytest.fail("no screen without adb")

    assert refused.value.code == "emulator_not_found"


@pytest.mark.usefixtures("server_file", "adb", "spawned")
@pytest.mark.asyncio
async def test_a_line_held_open_without_the_byte_still_times_out(
    phone: tuple[_Phone, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    phone[0].silent = True
    phone[0].all_closed.set()
    monkeypatch.setattr(settings.emulator, "stream_connect_timeout_seconds", 0.3)

    async def open_it() -> None:
        async with open_screen(_SERIAL, control=False):
            pytest.fail("no screen without the server's answer")

    with pytest.raises(EmulatorError) as refused:
        await asyncio.wait_for(open_it(), 3)

    assert refused.value.code == "emulator_screen_timeout"


@pytest.mark.usefixtures("server_file")
@pytest.mark.asyncio
async def test_a_second_cancel_does_not_cut_the_cleanup_short(
    phone: tuple[_Phone, int], adb: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow = _ServerProcess(exits_with_sockets=False, phone=phone[0])

    async def spawn(*_argv: str, **_streams: object) -> _ServerProcess:
        return slow

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(scrcpy_module, "_EXIT_GRACE_SECONDS", 0.2)
    entered = asyncio.Event()

    async def watch() -> None:
        async with open_screen(_SERIAL, control=False):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(watch())
    await entered.wait()
    # The first cancel ends the viewer; the next ones land inside the cleanup's grace
    # wait, as uvicorn's repeated cancels on shutdown do.
    for _ in range(3):
        task.cancel()
        await asyncio.sleep(0.03)
    with pytest.raises(asyncio.CancelledError):
        await task

    assert slow.killed
    assert _adb_steps(adb)[-1][:2] == ["forward", "--remove"]
