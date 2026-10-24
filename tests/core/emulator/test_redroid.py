"""redroid under Docker: labelled Android containers in a gateway's namespace, scrcpy."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from core.config import settings
from core.device_fingerprint_android import generate_android_fingerprint
from core.emulator import _profile, _topology, adb, redroid
from core.emulator._errors import EmulatorError
from core.emulator._profile import (
    aosp_boot_properties,
    build_battery_stealth_commands,
    build_cellular_stealth_commands,
    build_container_isolation_commands,
    build_usb_stealth_commands,
    container_spec,
)
from core.emulator._sensors import SENSORS_RC
from tests.core.emulator.test_topology import CHIP

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from tests.core.emulator.conftest import Console

# A phone without a chip: the host's CPUs, ``cpu_cores`` of them.
_SPEC = container_spec(
    generate_android_fingerprint("acc", None).model_copy(
        update={"device_model": "GooglePixel 7", "system_version": "SDK 33"},
    ),
    "DE",
)._replace(soc=None)
_CHIP_SPEC = _SPEC._replace(soc="chip")
_PS = "0|telebuba-acc-1|running\n2|telebuba-acc-2|exited\n|stray|running\n"
_IMAGE_PROP = "ro.product.product.model=redroid13_x86_64\nro.build.characteristics=default"


def _product_prop(image_prop: str, written: list[str]) -> Callable[[list[str]], str]:
    """``docker cp`` out hands over ``image_prop``; ``docker cp`` in records what came back."""

    def answer(argv: list[str]) -> str:
        if argv[1] == "cp" and argv[2].endswith(SENSORS_RC):  # no HAL from an earlier start
            code = "emulator_command_failed"
            raise EmulatorError(code)
        if argv[1] == "cp" and argv[2].startswith("telebuba-"):  # container -> host
            Path(argv[3]).write_text(image_prop, encoding="utf-8")
        elif argv[1] == "cp" and argv[3].endswith("build.prop"):  # read while it still exists
            written.append(Path(argv[2]).read_text(encoding="utf-8"))
        return _PS if argv[1] == "ps" else ""

    return answer


@pytest.fixture
def ps(console: Console) -> Console:
    console.answer = lambda argv: _PS if argv[1] == "ps" else ""
    return console


def _docker_calls(console: Console, verb: str) -> list[list[str]]:
    return [call for call in console.calls if call[1] == verb]


def _feature_copies(console: Console) -> list[list[str]]:
    """The ``docker cp`` of the feature files: a directory's contents, merged into Android's."""
    return [c for c in _docker_calls(console, "cp") if c[-1].endswith(":/vendor/etc/permissions")]


def test_only_labelled_lines_with_an_index_are_containers() -> None:
    assert redroid.parse_list(_PS + "garbage\n") == [
        (0, "telebuba-acc-1", True, False),
        (2, "telebuba-acc-2", False, False),
    ]


def test_a_paused_container_is_neither_running_nor_gone() -> None:
    assert redroid.parse_list("1|telebuba-acc-1|paused\n") == [(1, "telebuba-acc-1", False, True)]


def test_the_adb_serial_is_the_loopback_port_of_the_index() -> None:
    assert redroid.adb_serial(0) == "127.0.0.1:5600"
    assert redroid.adb_serial(3) == "127.0.0.1:5603"


@pytest.mark.asyncio
async def test_a_container_is_found_by_name_among_the_apps_own(ps: Console) -> None:
    instance = await redroid.find_instance("telebuba-acc-2")

    assert instance == (2, "telebuba-acc-2", False, False)
    listing = ps.calls[0]
    assert listing[0] == str(settings.emulator.docker_path)
    assert listing[1:5] == ["ps", "-a", "--filter", "label=telebuba.emulator"]


@pytest.mark.asyncio
async def test_a_new_container_joins_its_gateway_and_presents_the_phone(
    ps: Console,
) -> None:
    copied: list[str] = []
    ps.answer = _product_prop(_IMAGE_PROP, copied)

    instance = await redroid.create_instance(
        "telebuba-new", _SPEC, index=1, gateway="telebuba-new-net"
    )

    assert instance == (1, "telebuba-new", False, False)
    create = _docker_calls(ps, "create")[0]
    assert create[create.index("--network") + 1] == "container:telebuba-new-net"
    assert "--publish" not in create, "adb is the gateway's port"
    assert "telebuba.index=1" in create
    assert "--privileged" in create
    assert create[create.index("--cgroupns") + 1] == "private", "no host cgroups on cgroup v1"
    assert create[create.index("--cpus") + 1] == "2"
    assert "--cpuset-cpus" not in create
    assert "--volume" not in create, "the host's own CPUs show through"
    image = create.index("redroid/redroid:13.0.0_64only-latest")
    assert create[image - 2 : image] == ["--entrypoint", "/init"], "the image's names redroid"
    assert create[image + 1 :] == [
        "qemu=1",
        "androidboot.hardware=panther",
        f"androidboot.redroid_width={_SPEC.screen.width}",
        f"androidboot.redroid_height={_SPEC.screen.height}",
        f"androidboot.redroid_dpi={_SPEC.screen.dpi}",
        f"androidboot.serialno={_SPEC.serial}",
        "androidboot.use_memfd=true",
        "androidboot.redroid_gpu_mode=guest",
    ]
    assert "--device" not in create, "software drawing needs no GPU"
    take, give = [c for c in _docker_calls(ps, "cp") if c[-1].endswith("build.prop")]
    assert take[2] == give[3] == "telebuba-new:/product/etc/build.prop", "read last by init"
    ours = "".join(f"{key}={value}\n" for key, value in aosp_boot_properties(_SPEC).items())
    (written,) = copied
    assert written.startswith(_IMAGE_PROP + "\n"), "the image's own lines are kept"
    assert written.endswith(ours), "ours come after them, so they win"
    assert "ro.product.product.model=Pixel 7\n" in ours
    assert "ro.product.model=Pixel 7\n" in ours, (
        "a model with a space survives: it is not on the command line"
    )
    assert ps.calls.index(give) > ps.calls.index(create), "written before the first boot"
    (features,) = _feature_copies(ps)
    assert ps.calls.index(features) > ps.calls.index(create)


@pytest.mark.asyncio
async def test_a_container_on_the_host_gpu_is_given_its_device(ps: Console) -> None:
    ps.answer = _product_prop(_IMAGE_PROP, [])

    await redroid.create_instance(
        "telebuba-new", _SPEC, index=1, gateway="telebuba-new-net", gpu="host"
    )

    create = _docker_calls(ps, "create")[0]
    image = create.index("redroid/redroid:13.0.0_64only-latest")
    assert create[create.index("--device") + 1] == "/dev/dri:/dev/dri"
    assert create.index("--device") < image, "a docker option, not a boot argument"
    assert create[-1] == "androidboot.redroid_gpu_mode=host"


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
async def test_a_refresh_rewrites_the_stopped_containers_identity(ps: Console) -> None:
    first = redroid.with_identity(_IMAGE_PROP, redroid.build_prop(_SPEC))
    copied: list[str] = []
    ps.answer = _product_prop(first, copied)
    moved = _SPEC._replace(region=_SPEC.region._replace(timezone="Europe/Warsaw"))

    await redroid.refresh_identity("telebuba-acc-2", moved, 2)

    (written,) = copied
    assert written == redroid.with_identity(_IMAGE_PROP, redroid.build_prop(moved))
    assert "persist.sys.timezone=Europe/Warsaw\n" in written
    assert "Europe/Berlin" not in written, "the earlier block is replaced, not stacked"
    assert _docker_calls(ps, "update") == [], "a phone without a chip keeps its CPUs"
    assert len(_feature_copies(ps)) == 1, "a container made before gets them too"


@pytest.fixture
def chip(ps: Console, monkeypatch: pytest.MonkeyPatch) -> Console:
    """``CHIP`` (4 cores) is the phone's chip; Docker's VM has 16 CPUs."""
    for module in (_topology, _profile):
        monkeypatch.setattr(module, "load_socs", lambda: {"chip": CHIP})
    product = _product_prop(_IMAGE_PROP, [])
    ps.answer = lambda argv: "16\n" if argv[1] == "info" else product(argv)
    return ps


@pytest.mark.asyncio
async def test_a_new_container_with_a_chip_is_pinned_and_sees_its_topology(chip: Console) -> None:
    await redroid.create_instance("telebuba-new", _CHIP_SPEC, index=1, gateway="telebuba-new-net")

    create = _docker_calls(chip, "create")[0]
    assert "--cpus" not in create
    assert create[create.index("--cpuset-cpus") + 1] == "4-7", "pod 1's own slice"
    directory = settings.emulator.cpu_topology_dir.resolve() / "chip-4"
    mounts = [create[i + 1] for i, arg in enumerate(create) if arg == "--volume"]
    assert mounts == [
        f"{directory / 'cpu'}:/sys/devices/system/cpu:ro",
        f"{directory / 'cpuinfo'}:/dev/cpuinfo:ro",
    ]
    assert (directory / "cpu/cpu3/cpufreq/cpuinfo_max_freq").read_text() == "3187200\n"
    (rc,) = [c for c in _docker_calls(chip, "cp") if c[-1].endswith("cpuinfo.rc")]
    assert rc[2:] == [str(directory / "cpuinfo.rc"), "telebuba-new:/vendor/etc/init/cpuinfo.rc"]
    assert chip.calls.index(rc) > chip.calls.index(create)


@pytest.mark.asyncio
async def test_a_refresh_pins_a_chips_container_without_new_mounts(chip: Console) -> None:
    await redroid.refresh_identity("telebuba-acc-2", _CHIP_SPEC, 2)

    (update,) = _docker_calls(chip, "update")
    assert update[2:] == ["--cpus", "4", "--cpuset-cpus", "8-11", "telebuba-acc-2"], (
        "pod 2's own slice, its old --cpus quota raised to all of it"
    )
    assert not [c for c in chip.calls if c[1] in {"create", "rm"}], "nothing is made again"
    assert not [c for c in _docker_calls(chip, "cp") if c[-1].endswith("cpuinfo.rc")]


@pytest.mark.asyncio
async def test_a_launch_starts_the_named_container_and_drops_adbs_stale_connection(
    ps: Console,
) -> None:
    await redroid.launch_instance("telebuba-acc-2", 2)

    assert _docker_calls(ps, "start")[0][2] == "telebuba-acc-2"
    assert ps.calls[-1][1:] == ["disconnect", "127.0.0.1:5602"]
    assert not _docker_calls(ps, "ps"), "started by its name, not looked up by its index"


@pytest.mark.usefixtures("ps")
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
    await redroid.show_instance("telebuba-acc-1", 0)
    await redroid.show_instance("telebuba-acc-1", 0)

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
async def test_no_window_opens_while_the_desktop_viewer_is_off(
    viewers: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "desktop_viewer", False)

    await redroid.show_instance("telebuba-acc-1", 0)

    assert viewers == [], "a window would encode video for as long as it stays open"


@pytest.mark.usefixtures("ps")
@pytest.mark.asyncio
async def test_a_closed_window_is_opened_again(viewers: list[list[str]]) -> None:
    assert redroid.viewer_open("telebuba-acc-1") is False
    await redroid.show_instance("telebuba-acc-1", 0)
    viewer = redroid._VIEWERS["telebuba-acc-1"]
    assert isinstance(viewer, _Viewer)
    assert redroid.viewer_open("telebuba-acc-1") is True
    viewer.returncode = 0  # the operator closed the window
    assert redroid.viewer_open("telebuba-acc-1") is False

    await redroid.show_instance("telebuba-acc-1", 0)

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
        await redroid.show_instance("telebuba-acc-1", 0)

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
        await redroid.show_instance("telebuba-acc-1", 0)

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


def test_profile_command_builders_include_all_stage3_requirements() -> None:
    isolation = "\n".join(
        build_container_isolation_commands("11111111-2222-4333-8444-555555555555")
    )
    assert "rm -f /.dockerenv" in isolation
    assert "mount --bind -o ro /dev/.boot_id /proc/sys/kernel/random/boot_id" in isolation

    usb_cmds = "\n".join(build_usb_stealth_commands())
    assert "settings put global development_settings_enabled 0" in usb_cmds
    assert "setprop sys.usb.state mtp" in usb_cmds
    assert "setprop sys.usb.config mtp" in usb_cmds

    battery_cmds = "\n".join(build_battery_stealth_commands(level=65, temp=320))
    assert "dumpsys battery set present 1" in battery_cmds
    assert "dumpsys battery set status 3" in battery_cmds
    assert "dumpsys battery set ac 0" in battery_cmds
    assert "dumpsys battery set usb 0" in battery_cmds
    assert "dumpsys battery set wireless 0" in battery_cmds
    assert "dumpsys battery set level 65" in battery_cmds
    assert "dumpsys battery set temp 320" in battery_cmds
    assert battery_cmds.endswith("dumpsys battery set present 1"), "after the level, not at 0"

    cellular_cmds = "\n".join(build_cellular_stealth_commands())
    assert "setprop gsm.network.type LTE" in cellular_cmds
    assert "setprop gsm.sim.state READY" in cellular_cmds


@pytest.mark.asyncio
async def test_stealth_script_keeps_adb_and_needs_no_resetprop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # redroid ships no resetprop (exit 127 failed every open), and adb_enabled=0 stops
    # adbd for good: the setting persists in /data and the app loses the phone.
    expected_id = "11111111-2222-4333-8444-555555555555"
    mock_shell = AsyncMock(side_effect=["", expected_id, "ok"])
    monkeypatch.setattr(adb, "shell", mock_shell)

    await adb.apply_stealth_profile("127.0.0.1:5555", boot_id=expected_id)

    script = mock_shell.await_args_list[0].args[1]
    assert "resetprop" not in script
    assert "adb_enabled" not in script


@pytest.mark.asyncio
async def test_apply_stealth_profile_functional_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    expected_id = "11111111-2222-4333-8444-555555555555"
    mock_shell = AsyncMock(side_effect=["", expected_id, "ok"])
    monkeypatch.setattr(adb, "shell", mock_shell)

    boot_id = await adb.apply_stealth_profile("127.0.0.1:5555", boot_id=expected_id)
    assert boot_id == expected_id
    assert mock_shell.await_count == 3


@pytest.mark.asyncio
async def test_apply_stealth_profile_raises_emulator_error_on_boot_id_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_id = "11111111-2222-4333-8444-555555555555"
    mock_shell = AsyncMock(side_effect=["", "wrong-host-boot-id", "ok"])
    monkeypatch.setattr(adb, "shell", mock_shell)

    with pytest.raises(EmulatorError) as exc_info:
        await adb.apply_stealth_profile("127.0.0.1:5555", boot_id=expected_id)
    assert exc_info.value.code == "emulator_command_failed"


@pytest.mark.asyncio
async def test_apply_stealth_profile_raises_emulator_error_on_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_id = "11111111-2222-4333-8444-555555555555"
    monkeypatch.setattr(adb, "shell", AsyncMock(side_effect=["", expected_id, ""]))
    with pytest.raises(EmulatorError) as exc_info:
        await adb.apply_stealth_profile("127.0.0.1:5555", boot_id=expected_id)
    assert exc_info.value.code == "emulator_command_failed"
