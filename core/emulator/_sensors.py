"""The phone's sensors: a sensors HAL reporting its real chips, fed by a simulated hand.

redroid ships no sensors HAL, so ``SensorManager`` finds nothing. ``docker/sensors-hal``
builds one (an AIDL ``android.hardware.sensors`` service, NDK only, see docs/redroid.md)
once per architecture; ``EMULATOR__SENSORS_HAL_DIR`` is where its output went, empty
for none. A container gets the binary for its host's architecture with the files that
make init start it and servicemanager accept it (``write_vendor_tree``), copied into
its ``/vendor`` before boot.

What it reports comes from ``configs/oem_sensors``: one sheet per phone, read from
public dumps of that phone's sensor list (names, vendors, ranges, delays), never made
up. A field no dump showed is ``null`` there and takes its kind's usual value here
(``_KINDS``). The readings follow a seed derived from the phone's serial (so from the
account), held in a hand or lying on a desk, under the Earth's field where the proxy is.
"""

from __future__ import annotations

import hashlib
import logging
import math
import shutil
from functools import cache
from typing import TYPE_CHECKING, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field

from core.config import settings
from core.emulator._blueprints import CONFIGS_DIR
from core.emulator._geo import COUNTRY_COORDINATES
from core.emulator._process import docker

if TYPE_CHECKING:
    from pathlib import Path

    from core.emulator._profile import ContainerSpec

_SHEETS_DIR = CONFIGS_DIR / "oem_sensors"
# AOSP's AIDL multi-HAL service name: what Pixels run, nothing a detector looks for.
HAL_NAME = "android.hardware.sensors-service.multihal"
SENSORS_CONF = "/vendor/etc/sensors/sensors.conf"
SENSORS_RC = f"/vendor/etc/init/{HAL_NAME}.rc"
_RC = f"""service vendor.sensors-hal-multihal /vendor/bin/hw/{HAL_NAME} {SENSORS_CONF}
    class hal
    user system
    group system
    interface aidl android.hardware.sensors.ISensors/default
"""
# Version 2 is Android 14's (compatibility matrix 8); 13's sensorservice reads it as well.
_MANIFEST = """<manifest version="1.0" type="device">
    <hal format="aidl">
        <name>android.hardware.sensors</name>
        <version>2</version>
        <fqname>ISensors/default</fqname>
    </hal>
</manifest>
"""
# docker info's architecture -> the build's directory (an Android ABI).
ARM64_ABI = "arm64-v8a"
_ABIS = {"x86_64": "x86_64", "aarch64": ARM64_ABI, "arm64": ARM64_ABI}
# A phone's share in a hand (else on a desk) and its room's light (lux, log-uniform).
_HAND_SHARE = 0.75
_LUX = (60.0, 450.0)
# The IGRF-13 (2020) centred dipole: its north pole and its field at the equator, uT.
_DIPOLE_POLE = (80.65, -72.68)
_DIPOLE_UT = 29.4
# Where the proxy's country is unknown: mid-latitude Europe.
_DEFAULT_PLACE = (48.0, 10.0)
_WAKE_UP = 1
_ON_CHANGE = 2
# ``host_abi``, once asked; process-local like the rest of the runtime state.
_host_abi: str | None = None

SensorKind = Literal[
    "accelerometer",
    "magnetometer",
    "gyroscope",
    "light",
    "proximity",
    "gravity",
    "linear_acceleration",
    "rotation_vector",
    "game_rotation_vector",
]

# Stdlib sink, names and codes only (see ``core.emulator._errors``).
logger = logging.getLogger(__name__)


class _Kind(NamedTuple):
    """A kind of sensor: its Android type, how it reports, and its usual values."""

    android_type: int
    flags: int
    feature: str | None
    max_range: float
    resolution: float
    power_ma: float
    min_delay_us: int
    max_delay_us: int


_KINDS: dict[SensorKind, _Kind] = {
    "accelerometer": _Kind(1, 0, "accelerometer", 78.4532, 0.0023942017, 0.15, 2500, 1_000_000),
    "magnetometer": _Kind(2, 0, "compass", 4912.0, 0.15, 1.1, 10000, 1_000_000),
    "gyroscope": _Kind(4, 0, "gyroscope", 34.906586, 0.0012217305, 0.55, 2500, 1_000_000),
    "light": _Kind(5, _ON_CHANGE, "light", 60000.0, 1.0, 0.75, 0, 0),
    "proximity": _Kind(8, _ON_CHANGE | _WAKE_UP, "proximity", 5.0, 1.0, 0.75, 0, 0),
    "gravity": _Kind(9, 0, None, 19.6133, 0.0023942017, 0.5, 5000, 1_000_000),
    "linear_acceleration": _Kind(10, 0, None, 78.4532, 0.0023942017, 0.5, 5000, 1_000_000),
    "rotation_vector": _Kind(11, 0, None, 1.0, 5.9604645e-08, 1.1, 5000, 1_000_000),
    "game_rotation_vector": _Kind(15, 0, None, 1.0, 5.9604645e-08, 0.5, 5000, 1_000_000),
}


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class OemSensor(_Frozen):
    """One sensor as the phone lists it; ``None``: no dump showed that field."""

    type: SensorKind
    # Tabs and line breaks would split the HAL's config line.
    name: str = Field(pattern=r"^[^\t\r\n]+$")
    vendor: str = Field(pattern=r"^[^\t\r\n]+$")
    version: int | None = Field(default=None, ge=1)
    max_range: float | None = Field(default=None, gt=0)
    resolution: float | None = Field(default=None, gt=0)
    power_ma: float | None = Field(default=None, ge=0)
    min_delay_us: int | None = Field(default=None, ge=0)
    max_delay_us: int | None = Field(default=None, ge=0)
    fifo_max_events: int | None = Field(default=None, ge=0)
    source: str = Field(pattern=r"^https://\S+$")
    # Read from a sibling model with the same chips, where the phone's own was not found.
    sibling: str | None = None


class SensorSheet(_Frozen):
    device: str = Field(pattern=r"^[A-Za-z0-9_]+$")
    sources: tuple[str, ...] = Field(min_length=1)
    sensors: tuple[OemSensor, ...] = Field(min_length=1)


@cache
def load_sensor_sheets() -> dict[str, SensorSheet]:
    """Every sheet by its phone's ``device``, validated; read once per process."""
    sheets = (
        SensorSheet.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(_SHEETS_DIR.glob("*.json"))
    )
    return {sheet.device: sheet for sheet in sheets}


def geomagnetic_field(latitude: float, longitude: float) -> tuple[float, float, float]:
    """The Earth's field (east, north, up; uT) at a place, as the 2020 centred dipole has it.

    Within some 15 % of the real field's strength and dip: plausible for a compass,
    not a survey. Declination is the dipole's own.
    """
    lat, lon = math.radians(latitude), math.radians(longitude)
    pole_lat, pole_lon = (math.radians(v) for v in _DIPOLE_POLE)
    # Geomagnetic latitude, and the bearing of the dipole's north pole from the place.
    across = math.cos(pole_lat) * math.cos(pole_lon - lon)
    mag_lat = math.asin(math.sin(lat) * math.sin(pole_lat) + math.cos(lat) * across)
    bearing = math.atan2(
        math.sin(pole_lon - lon) * math.cos(pole_lat),
        math.cos(lat) * math.sin(pole_lat) - math.sin(lat) * across,
    )
    horizontal = _DIPOLE_UT * math.cos(mag_lat)
    down = 2 * _DIPOLE_UT * math.sin(mag_lat)
    return (horizontal * math.sin(bearing), horizontal * math.cos(bearing), -down)


def _number(value: float) -> str:
    return f"{value:.10g}"


def _sensor_line(handle: int, sensor: OemSensor) -> str:
    kind = _KINDS[sensor.type]
    fields = [
        "sensor",
        str(handle),
        str(kind.android_type),
        str(kind.flags),
        str(sensor.version or 1),
        _number(sensor.max_range or kind.max_range),
        _number(sensor.resolution or kind.resolution),
        _number(kind.power_ma if sensor.power_ma is None else sensor.power_ma),
        str(kind.min_delay_us if sensor.min_delay_us is None else sensor.min_delay_us),
        str(kind.max_delay_us if sensor.max_delay_us is None else sensor.max_delay_us),
        "0",
        str(sensor.fifo_max_events or 0),
        sensor.name,
        sensor.vendor,
    ]
    return "\t".join(fields)


def hal_config(spec: ContainerSpec) -> str | None:
    """The HAL's config for ``spec``'s phone (docker/sensors-hal/src/sensors.cpp), or ``None``.

    ``None`` when no sheet lists the phone's sensors.
    """
    sheet = load_sensor_sheets().get(spec.device)
    if sheet is None:
        return None
    seed = int.from_bytes(hashlib.sha256(f"sensors:{spec.serial}".encode()).digest()[:8], "big")
    share = (seed >> 32) / 2**32
    low, high = _LUX
    lux = low * (high / low) ** ((seed & 0xFFFF) / 0xFFFF)
    country = (spec.region.country or "").upper()
    east, north, up = geomagnetic_field(*COUNTRY_COORDINATES.get(country, _DEFAULT_PLACE))
    lines = [
        f"seed\t{seed}",
        f"pose\t{'hand' if share < _HAND_SHARE else 'desk'}",
        f"lux\t{_number(round(lux))}",
        f"field\t{east:.2f}\t{north:.2f}\t{up:.2f}",
        *(_sensor_line(handle, sensor) for handle, sensor in enumerate(sheet.sensors, start=1)),
    ]
    return "\n".join(lines) + "\n"


def _feature_xml(feature: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<permissions>\n'
        f'    <feature name="android.hardware.sensor.{feature}" />\n</permissions>\n'
    )


def _write_files(directory: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def write_disabled_tree(directory: Path) -> None:
    """Write over ``write_vendor_tree``'s files what turns an installed HAL off.

    A stopped container's files cannot be deleted from outside, only replaced: an init
    script with no service, a VINTF fragment with no HAL, no config, and no features.
    The binary stays, never started.
    """
    files = {
        f"etc/init/{HAL_NAME}.rc": "",
        f"etc/vintf/manifest/{HAL_NAME}.xml": '<manifest version="1.0" type="device" />\n',
        "etc/sensors/sensors.conf": "",
    }
    for kind in _KINDS.values():
        if kind.feature is not None:
            files[f"etc/permissions/android.hardware.sensor.{kind.feature}.xml"] = (
                '<?xml version="1.0" encoding="utf-8"?>\n<permissions />\n'
            )
    _write_files(directory, files)


def write_vendor_tree(directory: Path, spec: ContainerSpec, binary: Path) -> bool:
    """Write under ``directory`` what goes into the container's ``/vendor``; False: nothing.

    The service (``binary``), its init script and VINTF declaration, its config, and the
    ``android.hardware.sensor.*`` features of the sensors it serves.
    """
    config = hal_config(spec)
    if config is None:
        return False
    files = {
        f"etc/init/{HAL_NAME}.rc": _RC,
        f"etc/vintf/manifest/{HAL_NAME}.xml": _MANIFEST,
        "etc/sensors/sensors.conf": config,
    }
    for sensor in load_sensor_sheets()[spec.device].sensors:
        feature = _KINDS[sensor.type].feature
        if feature is not None:
            files[f"etc/permissions/android.hardware.sensor.{feature}.xml"] = _feature_xml(feature)
    _write_files(directory, files)
    service = directory / "bin" / "hw" / HAL_NAME
    service.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(binary, service)
    service.chmod(0o755)
    return True


async def host_abi() -> str | None:
    """The Android ABI of Docker's host (``None``: neither build fits); asked once."""
    global _host_abi  # noqa: PLW0603 - a process-wide fact about the Docker host
    if _host_abi is None:
        architecture = (await docker("info", "--format", "{{.Architecture}}")).strip()
        _host_abi = _ABIS.get(architecture, "")
    return _host_abi or None


async def hal_binary() -> Path | None:
    """The HAL built for this host, or ``None``: off, or not built for its architecture."""
    directory = settings.emulator.sensors_hal_dir
    if directory is None:
        return None
    abi = await host_abi()
    binary = directory / (abi or "unknown") / HAL_NAME
    if not binary.is_file():
        logger.warning("no sensors HAL built for %s: the phone boots without sensors", abi)
        return None
    return binary
