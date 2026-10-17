"""redroid under Docker: labelled Android containers in a gateway's namespace, scrcpy."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.emulator import adb, redroid
from core.emulator._errors import EmulatorError
from core.emulator._profile import ContainerSpec

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.core.emulator.conftest import Console

_SPEC = ContainerSpec("Google", "Pixel 7", 34, "490154203237518")
_PS = "0|telebuba-acc-1|running\n2|telebuba-acc-2|exited\n|stray|running\n"


@pytest.fixture
def ps(console: Console) -> Console:
    console.answer = lambda argv: _PS if argv[1] == "ps" else ""
    return console


def _docker_calls(console: Console, verb: str) -> list[list[str]]:
    return [call for call in console.calls if call[1] == verb]


def test_only_labelled_lines_with_an_index_are_containers() -> None:
    assert redroid.parse_list(_PS + "garbage\n") == [
        (0, "telebuba-acc-1", True),
        (2, "telebuba-acc-2", False),
    ]


def test_the_adb_serial_is_the_loopback_port_of_the_index() -> None:
    assert redroid.adb_serial(0) == "127.0.0.1:5600"
    assert redroid.adb_serial(3) == "127.0.0.1:5603"


@pytest.mark.asyncio
async def test_a_container_is_found_by_name_among_the_apps_own(ps: Console) -> None:
    instance = await redroid.find_instance("telebuba-acc-2")

    assert instance == (2, "telebuba-acc-2", False)
    listing = ps.calls[0]
    assert listing[0] == str(settings.emulator.docker_path)
    assert listing[1:5] == ["ps", "-a", "--filter", "label=telebuba.emulator"]


@pytest.mark.asyncio
async def test_a_new_container_joins_its_gateway_and_presents_the_phone(
    ps: Console,
) -> None:
    copied: list[str] = []

    def answer(argv: list[str]) -> str:
        if argv[1] == "cp":  # read while the scratch copy still exists
            copied.append((Path(argv[2]) / "build.prop").read_text(encoding="utf-8"))
        return _PS if argv[1] == "ps" else ""

    ps.answer = answer

    instance = await redroid.create_instance(
        "telebuba-new", _SPEC, index=1, gateway="telebuba-new-net"
    )

    assert instance == (1, "telebuba-new", False)
    create = _docker_calls(ps, "create")[0]
    assert create[create.index("--network") + 1] == "container:telebuba-new-net"
    assert "--publish" not in create, "adb is the gateway's port"
    assert "telebuba.index=1" in create
    assert "--privileged" in create
    image = create.index("redroid/redroid:14.0.0-latest")
    assert create[image + 1 :] == [
        "androidboot.redroid_width=720",
        "androidboot.redroid_height=1280",
        "androidboot.redroid_dpi=320",
        "androidboot.use_memfd=true",
    ]
    copy = _docker_calls(ps, "cp")[0]
    assert copy[3] == "telebuba-new:/system_dlkm/"
    assert copied == ["ro.product.manufacturer=Google\nro.product.model=Pixel 7\n"], (
        "a model with a space survives: it is not on the command line"
    )
    assert ps.calls.index(copy) > ps.calls.index(create), "copied before the first boot"


@pytest.mark.asyncio
async def test_an_sdk_without_an_image_is_refused_before_anything_is_created(
    ps: Console,
) -> None:
    with pytest.raises(EmulatorError) as refused:
        await redroid.create_instance(
            "telebuba-new", _SPEC._replace(sdk=31), index=1, gateway="telebuba-new-net"
        )

    assert refused.value.code == "emulator_sdk_mismatch"
    assert ps.calls == []


@pytest.mark.asyncio
async def test_an_identity_that_would_add_properties_is_refused(ps: Console) -> None:
    with pytest.raises(EmulatorError) as refused:
        await redroid.create_instance(
            "telebuba-new",
            _SPEC._replace(model="X\nro.secure=0"),
            index=1,
            gateway="telebuba-new-net",
        )

    assert refused.value.code == "emulator_device_unknown"
    assert ps.calls == []


@pytest.mark.asyncio
async def test_a_launch_starts_the_container_and_drops_adbs_stale_connection(
    ps: Console,
) -> None:
    await redroid.launch_instance(2)

    assert _docker_calls(ps, "start")[0][2] == "telebuba-acc-2"
    assert ps.calls[-1][1:] == ["disconnect", "127.0.0.1:5602"]


@pytest.mark.usefixtures("ps")
@pytest.mark.asyncio
async def test_an_unknown_index_is_a_failed_command() -> None:
    with pytest.raises(EmulatorError) as failed:
        await redroid.launch_instance(7)

    assert failed.value.code == "emulator_command_failed"


class _Viewer:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


@pytest.fixture
def viewers(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[list[str]]]:
    started: list[list[str]] = []

    async def spawn(*argv: str, **_streams: object) -> _Viewer:
        started.append(list(argv))
        return _Viewer()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    yield started
    redroid._VIEWERS.clear()


@pytest.mark.usefixtures("ps")
@pytest.mark.asyncio
async def test_the_container_is_shown_in_one_scrcpy_window(viewers: list[list[str]]) -> None:
    await redroid.show_instance(0)
    await redroid.show_instance(0)

    assert viewers == [
        [
            str(settings.emulator.scrcpy_path),
            "--serial",
            "127.0.0.1:5600",
            "--window-title",
            "telebuba-acc-1",
            "--no-audio",
        ],
    ]


@pytest.mark.usefixtures("ps")
@pytest.mark.asyncio
async def test_a_closed_window_is_opened_again(viewers: list[list[str]]) -> None:
    await redroid.show_instance(0)
    viewer = redroid._VIEWERS["telebuba-acc-1"]
    assert isinstance(viewer, _Viewer)
    viewer.returncode = 0  # the operator closed the window

    await redroid.show_instance(0)

    assert len(viewers) == 2


@pytest.mark.usefixtures("ps")
@pytest.mark.asyncio
async def test_a_missing_scrcpy_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def missing(*_argv: str, **_streams: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", missing)

    with pytest.raises(EmulatorError) as refused:
        await redroid.show_instance(0)

    assert refused.value.code == "emulator_not_found"


@pytest.mark.usefixtures("ps")
@pytest.mark.asyncio
async def test_a_scrcpy_that_cannot_start_is_a_failed_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def denied(*_argv: str, **_streams: object) -> None:
        raise PermissionError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", denied)

    with pytest.raises(EmulatorError) as refused:
        await redroid.show_instance(0)

    assert refused.value.code == "emulator_command_failed"


@pytest.mark.asyncio
async def test_adb_connects_to_a_network_serial_while_waiting_for_boot(console: Console) -> None:
    console.answer = lambda argv: "1" if "getprop" in argv else ""

    await adb.wait_for_boot("127.0.0.1:5600")
    await adb.connect_device("emulator-5554")

    connects = [call for call in console.calls if call[1] == "connect"]
    assert connects == [[str(settings.emulator.adb_path), "connect", "127.0.0.1:5600"]]


@pytest.mark.asyncio
async def test_a_refused_connection_is_left_to_the_next_adb_call(console: Console) -> None:
    def refuse(argv: list[str]) -> str:
        if argv[1] == "connect":
            code = "emulator_command_failed"
            raise EmulatorError(code)
        return ""

    console.answer = refuse

    await adb.connect_device("127.0.0.1:5600")
