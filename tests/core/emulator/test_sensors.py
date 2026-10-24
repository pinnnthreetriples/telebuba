"""The phone's sensors: its chips' sheet, the HAL's config, and the files copied before boot."""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.emulator import _sensors, redroid
from core.emulator._blueprints import load_blueprints
from core.emulator._errors import EmulatorError
from tests.core.emulator.test_redroid import _SPEC, _product_prop

if TYPE_CHECKING:
    from tests.core.emulator.conftest import Console

_ARCHITECTURE = "{{.Architecture}}"
# What Telegram's EmuDetector and its kind look for in names and files.
_EMULATOR_WORDS = (
    "goldfish",
    "ranchu",
    "qemu",
    "generic",
    "sdk",
    "nox",
    "vbox",
    "andy",
    "genymotion",
    "bluestacks",
    "emulator",
    "x86",
)


def _lines(text: str, kind: str) -> list[list[str]]:
    return [line.split("\t")[1:] for line in text.splitlines() if line.split("\t")[0] == kind]


def test_every_phone_has_a_sheet_of_its_real_sensors() -> None:
    sheets = _sensors.load_sensor_sheets()

    assert {blueprint.device for blueprint in load_blueprints()} <= set(sheets)
    for sheet in sheets.values():
        kinds = [sensor.type for sensor in sheet.sensors]
        assert len(kinds) == len(set(kinds)), "one sensor of each kind"
        assert {"accelerometer", "gyroscope", "magnetometer", "light", "proximity"} <= set(kinds)
        for sensor in sheet.sensors:
            assert sensor.source in sheet.sources
            text = f"{sensor.name} {sensor.vendor}".lower()
            assert not [word for word in _EMULATOR_WORDS if word in text]


def test_the_hal_config_lists_the_phones_sensors_as_it_reports_them() -> None:
    config = _sensors.hal_config(_SPEC)

    assert config is not None
    sheet = _sensors.load_sensor_sheets()["panther"]
    lines = _lines(config, "sensor")
    assert [line[11] for line in lines] == [sensor.name for sensor in sheet.sensors]
    assert [line[12] for line in lines] == [sensor.vendor for sensor in sheet.sensors]
    assert [int(line[0]) for line in lines] == list(range(1, len(lines) + 1))
    by_name = {line[11]: line for line in lines}
    accelerometer = by_name["LSM6DSV Accelerometer"]
    # handle, type, flags, version, range, resolution, power, min/max delay, fifo
    assert accelerometer[1:11] == [
        "1", "0", "1", "156.9064", "0.0047856453", "0.001", "2084", "533334", "0", "0",
    ]  # fmt: skip
    assert by_name["TMD3719 Ambient Light"][1:3] == ["5", "2"], "on change"
    assert by_name["TMD3719 Proximity (wake-up)"][1:3] == ["8", "3"], "on change, wakes up"
    assert "\n" not in "".join(line[-1] for line in lines)


def test_a_field_the_source_left_out_takes_its_kinds_usual_value() -> None:
    spec = _SPEC._replace(device="fuxi")

    config = _sensors.hal_config(spec)

    assert config is not None
    proximity = next(line for line in _lines(config, "sensor") if line[1] == "8")
    assert proximity[2:9] == ["3", "1", "5", "1", "0.75", "0", "0"], (
        "no version, range, resolution, power or delays found for it"
    )


def test_a_phone_without_a_sheet_gets_no_hal() -> None:
    assert _sensors.hal_config(_SPEC._replace(device="unknown")) is None


def test_the_phone_keeps_its_seed_and_another_phone_gets_its_own() -> None:
    other = _SPEC._replace(serial=_SPEC.serial + "0")

    first = _sensors.hal_config(_SPEC)
    assert first == _sensors.hal_config(_SPEC)
    assert first is not None
    assert _lines(first, "seed") != _lines(_sensors.hal_config(other) or "", "seed")
    ((pose,),) = _lines(first, "pose")
    assert pose in {"hand", "desk"}
    ((lux,),) = _lines(first, "lux")
    assert 50 <= float(lux) <= 500


@pytest.mark.parametrize(
    ("latitude", "longitude", "inclination", "strength"),
    [(55.76, 37.62, (65, 78), (45, 60)), (-0.79, 113.92, (-30, 10), (28, 45))],
)
def test_the_magnetic_field_is_the_earths_where_the_proxy_is(
    latitude: float,
    longitude: float,
    inclination: tuple[float, float],
    strength: tuple[float, float],
) -> None:
    east, north, up = _sensors.geomagnetic_field(latitude, longitude)

    total = math.sqrt(east**2 + north**2 + up**2)
    dip = math.degrees(math.atan2(-up, math.hypot(east, north)))
    assert strength[0] <= total <= strength[1]
    assert inclination[0] <= dip <= inclination[1]
    assert north > 0, "it points north"


def test_the_field_follows_the_proxy_country() -> None:
    config = _sensors.hal_config(_SPEC)
    moved = _sensors.hal_config(_SPEC._replace(region=_SPEC.region._replace(country="br")))

    assert config is not None
    assert moved is not None
    ((_, _, up_de),) = _lines(config, "field")
    ((_, _, up_br),) = _lines(moved, "field")
    assert float(up_de) < -30, "Germany: steeply down"
    assert float(up_de) < float(up_br)


def test_the_vendor_tree_holds_the_service_its_declaration_and_features(tmp_path: Path) -> None:
    binary = tmp_path / "hal"
    binary.write_bytes(b"\x7fELF")
    tree = tmp_path / "vendor"

    assert _sensors.write_vendor_tree(tree, _SPEC, binary)

    service = tree / "bin/hw/android.hardware.sensors-service.multihal"
    assert service.read_bytes() == b"\x7fELF"
    rc = (tree / "etc/init/android.hardware.sensors-service.multihal.rc").read_text()
    assert "/vendor/bin/hw/android.hardware.sensors-service.multihal" in rc
    assert "interface aidl android.hardware.sensors.ISensors/default" in rc
    assert _sensors.SENSORS_CONF in rc
    manifest = (tree / f"etc/vintf/manifest/{_sensors.HAL_NAME}.xml").read_text()
    assert "<fqname>ISensors/default</fqname>" in manifest
    assert (tree / "etc/sensors/sensors.conf").read_text() == _sensors.hal_config(_SPEC)
    gyroscope = (tree / "etc/permissions/android.hardware.sensor.gyroscope.xml").read_text()
    assert '<feature name="android.hardware.sensor.gyroscope" />' in gyroscope
    for path in tree.rglob("*"):
        assert not [word for word in _EMULATOR_WORDS if word in str(path.relative_to(tree))]


def test_a_phone_without_a_sheet_gets_no_tree(tmp_path: Path) -> None:
    tree = tmp_path / "vendor"
    tree.mkdir()

    assert not _sensors.write_vendor_tree(tree, _SPEC._replace(device="unknown"), tmp_path)
    assert not list(tree.iterdir())


@pytest.fixture
def hal_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A ``docker build --output`` of docker/sensors-hal, with the x86_64 binary only."""
    directory = tmp_path / "sensors-hal"
    binary = directory / "x86_64" / _sensors.HAL_NAME
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"\x7fELF")
    monkeypatch.setattr(settings.emulator, "sensors_hal_dir", directory)
    monkeypatch.setattr(_sensors, "_host_abi", None)
    return directory


def _sensors_console(
    console: Console, architecture: str, *, installed: bool = False
) -> list[list[str]]:
    """``docker info`` answers ``architecture``; each copy into /vendor lists its entries.

    The container has the HAL's init script from an earlier start when ``installed``.
    """
    trees: list[list[str]] = []
    product = _product_prop("ro.a=b")

    def answer(argv: list[str]) -> str:
        if argv[1:4] == ["info", "--format", _ARCHITECTURE]:
            return architecture + "\n"
        if argv[1] == "cp" and argv[2].endswith(f"/{_sensors.HAL_NAME}.rc"):
            if not installed:
                code = "emulator_command_failed"
                raise EmulatorError(code)
            Path(argv[3]).write_text("service ...\n", encoding="utf-8")
            return ""
        if argv[1] == "cp" and argv[-1].endswith(":/vendor"):
            trees.append(sorted(console.archive(console.calls[-1])))
            return ""
        return product(argv)

    console.answer = answer
    return trees


@pytest.mark.usefixtures("hal_dir")
@pytest.mark.asyncio
async def test_a_new_container_gets_the_sensors_hal_before_it_boots(console: Console) -> None:
    trees = _sensors_console(console, "x86_64")

    await redroid.create_instance("telebuba-new", _SPEC, index=1, gateway="telebuba-new-net")

    (tree,) = trees
    assert "bin/hw/android.hardware.sensors-service.multihal" in tree
    assert "etc/sensors/sensors.conf" in tree
    copy = next(call for call in console.calls if call[-1] == "telebuba-new:/vendor")
    create = next(call for call in console.calls if call[1] == "create")
    assert console.calls.index(copy) > console.calls.index(create)
    infos = [call for call in console.calls if _ARCHITECTURE in call]
    assert infos == [[str(settings.emulator.docker_path), "info", "--format", _ARCHITECTURE]]
    entries = console.archive(copy)
    owners = {
        name: (info.mode, info.uid, info.gid)
        for name, (info, _data) in entries.items()
        if name.startswith("bin") or name.endswith(".rc")
    }
    assert owners == {
        "bin": (0o751, 0, 2000),
        "bin/hw": (0o751, 0, 2000),
        f"bin/hw/{_sensors.HAL_NAME}": (0o755, 0, 2000),
        f"etc/init/{_sensors.HAL_NAME}.rc": (0o644, 0, 0),
    }, "as the image's own /vendor: root:shell directories, no executable init script"


@pytest.mark.usefixtures("hal_dir")
@pytest.mark.asyncio
async def test_a_refresh_copies_the_hal_again_with_the_new_countrys_field(
    console: Console,
) -> None:
    trees = _sensors_console(console, "x86_64")
    moved = _SPEC._replace(region=_SPEC.region._replace(country="br"))

    await redroid.refresh_identity("telebuba-acc-2", moved, 2)
    await redroid.refresh_identity("telebuba-acc-2", moved, 2)

    assert len(trees) == 2
    infos = [call for call in console.calls if _ARCHITECTURE in call]
    assert len(infos) == 1, "the host's architecture is asked once per process"


@pytest.mark.usefixtures("hal_dir")
@pytest.mark.asyncio
async def test_a_host_without_its_architectures_build_boots_without_sensors(
    console: Console, caplog: pytest.LogCaptureFixture
) -> None:
    trees = _sensors_console(console, "aarch64")

    with caplog.at_level(logging.WARNING):
        await redroid.create_instance("telebuba-new", _SPEC, index=1, gateway="telebuba-new-net")

    assert trees == []
    assert "arm64-v8a" in caplog.text


@pytest.mark.asyncio
async def test_no_hal_is_copied_while_it_is_off(console: Console) -> None:
    trees = _sensors_console(console, "x86_64")

    await redroid.create_instance("telebuba-new", _SPEC, index=1, gateway="telebuba-new-net")

    assert trees == []


def test_a_disabled_tree_leaves_init_nothing_to_start(tmp_path: Path) -> None:
    _sensors.write_disabled_tree(tmp_path)

    rc = (tmp_path / f"etc/init/{_sensors.HAL_NAME}.rc").read_text()
    assert "service" not in rc
    manifest = (tmp_path / f"etc/vintf/manifest/{_sensors.HAL_NAME}.xml").read_text()
    assert "<hal" not in manifest
    assert "<manifest" in manifest, "still a valid fragment"
    assert (tmp_path / "etc/sensors/sensors.conf").read_text() == ""
    gyroscope = (tmp_path / "etc/permissions/android.hardware.sensor.gyroscope.xml").read_text()
    assert "<feature" not in gyroscope
    assert not (tmp_path / "bin").exists()


@pytest.mark.asyncio
async def test_a_refresh_with_the_hal_off_disables_one_an_earlier_start_installed(
    console: Console,
) -> None:
    trees = _sensors_console(console, "x86_64", installed=True)

    await redroid.refresh_identity("telebuba-acc-2", _SPEC, 2)

    (tree,) = trees
    assert f"etc/init/{_sensors.HAL_NAME}.rc" in tree
    assert not [path for path in tree if path.startswith("bin/")]
