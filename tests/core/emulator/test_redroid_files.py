"""What a container is given before it boots: files as AOSP's, a battery, a phone's cores."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.emulator import _profile, _provision, _topology, redroid
from core.emulator._errors import EmulatorError
from core.emulator._sensors import SENSORS_RC
from tests.core.emulator.test_redroid import _CHIP_SPEC, _SPEC
from tests.core.emulator.test_topology import CHIP

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.core.emulator.conftest import Console

# The image's product build.prop: AOSP's comments, a value of ours, one of its own.
_IMAGE_PROP = (
    "####################################\n"
    "# from generate-common-build-props\n"
    "####################################\n"
    "ro.product.product.model=redroid13_x86_64\n"
    "ro.build.characteristics=default\n"
    "# end of file\n"
)
# What earlier versions wrote between the image's lines and their block.
_LEGACY_MARK = "# telebuba: the phone this container presents\n"


def _docker(image_prop: str, cores: int = 16) -> Callable[[list[str]], str]:
    """A docker whose containers hold ``image_prop`` and whose VM has ``cores`` CPUs."""

    def answer(argv: list[str]) -> str:
        if argv[1] == "cp" and argv[2].endswith(SENSORS_RC):
            code = "emulator_command_failed"
            raise EmulatorError(code)
        if argv[1] == "cp" and argv[2].endswith(":/product/etc/build.prop"):
            Path(argv[3]).write_text(image_prop, encoding="utf-8")
        return f"{cores}\n" if argv[1] == "info" else ""

    return answer


def _create(console: Console) -> list[str]:
    return next(call for call in console.calls if call[1] == "create")


def _prop(console: Console, name: str) -> str:
    (copy,) = console.copies_into(f"{name}:/product/etc")
    ((info, data),) = console.archive(copy).values()
    assert info.name == "build.prop"
    return data.decode("utf-8")


@pytest.mark.asyncio
async def test_the_identity_goes_in_as_the_images_own_file_without_a_mark(
    console: Console,
) -> None:
    console.answer = _docker(_IMAGE_PROP)

    await redroid.create_instance("telebuba-new", _SPEC, index=1, gateway="telebuba-new-net")

    (copy,) = console.copies_into("telebuba-new:/product/etc")
    ((info, _data),) = console.archive(copy).values()
    assert (info.mode, info.uid, info.gid) == (0o644, 0, 0), "as the image's, not Windows's 0755"
    written = _prop(console, "telebuba-new")
    assert "telebuba" not in written.lower(), "nothing names the app"
    assert "# from generate-common-build-props\n" in written, "AOSP's own comments stay"
    assert "ro.build.characteristics=default\n" in written, "the image's other lines stay"
    assert "redroid13_x86_64" not in written, "a line we set replaces the image's"
    assert written.endswith(redroid.build_prop(_SPEC))


@pytest.mark.asyncio
async def test_a_refresh_replaces_an_earlier_block_marked_or_not(console: Console) -> None:
    legacy = _IMAGE_PROP + _LEGACY_MARK + redroid.build_prop(_SPEC)
    console.answer = _docker(legacy)
    no_carrier = _SPEC._replace(region=_SPEC.region._replace(carrier=None))

    await redroid.refresh_identity("telebuba-acc-2", no_carrier, 2)

    written = _prop(console, "telebuba-acc-2")
    assert written == _provision.with_identity(_IMAGE_PROP, redroid.build_prop(no_carrier))
    assert "telebuba" not in written.lower(), "the old mark goes with its block"
    assert "gsm." not in written, "a carrier the proxy's country lost is not kept"
    again = _provision.with_identity(written, redroid.build_prop(_SPEC))
    assert again == _provision.with_identity(_IMAGE_PROP, redroid.build_prop(_SPEC)), (
        "an unmarked block is replaced key by key"
    )


@pytest.mark.asyncio
async def test_the_feature_files_and_the_overlay_are_roots_and_not_executable(
    console: Console,
) -> None:
    console.answer = _docker(_IMAGE_PROP)

    await redroid.create_instance("telebuba-new", _SPEC, index=1, gateway="telebuba-new-net")

    (features,) = console.copies_into("telebuba-new:/vendor/etc/permissions")
    (overlay,) = console.copies_into("telebuba-new:/product")
    for copy in (features, overlay):
        for name, (info, _data) in console.archive(copy).items():
            expected = (0o755 if info.isdir() else 0o644, 0, 0)
            assert (info.mode, info.uid, info.gid) == expected, name
    assert any(name.endswith(".apk") for name in console.archive(overlay))
    assert console.calls.index(features) > console.calls.index(_create(console))


@pytest.fixture
def chip(console: Console, monkeypatch: pytest.MonkeyPatch) -> Console:
    for module in (_topology, _profile):
        monkeypatch.setattr(module, "load_socs", lambda: {"chip": CHIP})
    console.answer = _docker(_IMAGE_PROP)
    return console


@pytest.mark.asyncio
async def test_the_cpuinfo_script_is_an_init_scripts_mode(chip: Console) -> None:
    await redroid.create_instance("telebuba-new", _CHIP_SPEC, index=1, gateway="telebuba-new-net")

    (copy,) = chip.copies_into("telebuba-new:/vendor/etc/init")
    ((info, data),) = chip.archive(copy).values()
    assert info.name == "cpuinfo.rc"
    assert (info.mode, info.uid, info.gid) == (0o644, 0, 0)
    assert b"/proc/cpuinfo bind" in data


@pytest.mark.asyncio
async def test_a_new_container_reads_its_battery_from_a_power_supply_tree(
    console: Console,
) -> None:
    console.answer = _docker(_IMAGE_PROP)

    await redroid.create_instance("telebuba-new", _SPEC, index=1, gateway="telebuba-new-net")

    create = _create(console)
    directory = settings.emulator.battery_dir.resolve() / "1"
    mounts = [create[i + 1] for i, arg in enumerate(create) if arg == "--volume"]
    assert f"{directory}:/sys/class/power_supply:ro" in mounts, "the health HAL reads it"
    battery = directory / "battery"
    assert (battery / "type").read_text(encoding="utf-8") == "Battery\n"
    assert 15 <= int((battery / "capacity").read_text(encoding="utf-8")) <= 100
    assert 3_600_000 <= int((battery / "voltage_now").read_text(encoding="utf-8")) <= 4_300_000


@pytest.mark.asyncio
async def test_a_refresh_writes_the_battery_again_without_new_mounts(console: Console) -> None:
    console.answer = _docker(_IMAGE_PROP)

    await redroid.refresh_identity("telebuba-acc-2", _SPEC, 2)

    battery = settings.emulator.battery_dir.resolve() / "2" / "battery"
    assert (battery / "voltage_now").is_file(), "the next boot's voltage follows its level"
    assert not [c for c in console.calls if c[1] == "create"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("cores", "cpuset"), [(16, "8-15"), (12, "0-7"), (4, "0-3")])
async def test_a_phone_without_a_chip_sees_a_phones_cores(
    console: Console, cores: int, cpuset: str
) -> None:
    console.answer = _docker(_IMAGE_PROP, cores)

    await redroid.create_instance("telebuba-new", _SPEC, index=1, gateway="telebuba-new-net")

    create = _create(console)
    assert create[create.index("--cpuset-cpus") + 1] == cpuset, "8 at most, pod 1's own slice"
    assert create[create.index("--cpus") + 1] == "2", "its share of them stays cpu_cores"


@pytest.mark.asyncio
async def test_a_refresh_pins_a_phone_without_a_chip_to_its_cores(console: Console) -> None:
    console.answer = _docker(_IMAGE_PROP)

    await redroid.refresh_identity("telebuba-acc-3", _SPEC, 3)

    (update,) = [c for c in console.calls if c[1] == "update"]
    assert update[2:] == ["--cpuset-cpus", "8-15", "telebuba-acc-3"], "its quota left alone"
