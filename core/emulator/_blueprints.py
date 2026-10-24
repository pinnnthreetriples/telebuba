"""The real phones a container can be, read from ``configs/oem_blueprints``.

One JSON per phone: who made it (brand, maker, model, product, device), how its
serial numbers look, the container's screen, its main camera, and one real build per Android SDK it
ran. A build is copied from a public firmware listing, never made up, and the
container claims exactly it. ``ro.build.fingerprint`` and ``ro.build.description``
are not stored: they are put together from these fields, so they cannot disagree.

A phone may name its system-on-chip (``soc``): one JSON per chip in ``configs/soc``,
read from a public dump of that chip's ``/proc/cpuinfo`` and cpufreq tables, from
which the container's CPU topology is made (``core.emulator._topology``).
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ``configs/`` at the repository root, next to ``core/``.
CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs"
_BLUEPRINTS_DIR = CONFIGS_DIR / "oem_blueprints"
_SOC_DIR = CONFIGS_DIR / "soc"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class OemBuild(_Frozen):
    """One firmware release of a phone (``ro.build.*``)."""

    release: str = Field(pattern=r"^\d{2}$")
    build_id: str = Field(pattern=r"^[A-Z0-9][A-Z0-9.]+$")
    incremental: str = Field(pattern=r"^[A-Za-z0-9._-]+$")
    security_patch: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    # Only where the phone's own differs from what is put together from the fields:
    # ``<product>-user <release> <build_id> <incremental> release-keys`` and ``build_id``.
    description: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._ -]+$")
    display_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._()-]+$")
    # ``Build.USER`` / ``Build.HOST`` / ``Build.TIME``: who built it, where, and when, as
    # the firmware prints them (``ro.build.date`` in the build server's zone).
    build_user: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    build_host: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]*$")
    build_date: str = Field(pattern=r"^[A-Z][a-z]{2} [A-Z][a-z]{2} [ \d]\d [\d:]{8} [A-Z]+ \d{4}$")
    build_date_utc: int = Field(gt=0)
    # Where the build was read: a public dump of that firmware's build.prop.
    source: str = Field(pattern=r"^https://\S+$")


class Screen(_Frozen):
    """The container's display: 720 px wide, the phone's aspect and width in dp."""

    width: int = Field(ge=320, le=1440)
    height: int = Field(ge=480, le=3200)
    dpi: int = Field(ge=120, le=640)


class Camera(_Frozen):
    """The main rear camera as its photos' EXIF reports it, and how the phone names them.

    Aperture and focal length are the maker's spec-sheet values for that lens.
    ``file_style`` is the stock camera app's file name: Samsung ``20240512_143022``,
    Pixel ``PXL_20240512_143022123`` (UTC), Xiaomi ``IMG_20240512_143022``, OnePlus
    ``IMG20240512143022``.
    """

    f_number: float = Field(ge=1.0, le=4.0)
    focal_length_mm: float = Field(ge=1.0, le=15.0)
    file_style: Literal["samsung", "pixel", "xiaomi", "oneplus"]


class CpuCores(_Frozen):
    """Cores of one kind, as ``/proc/cpuinfo`` names them (``CPU implementer`` ... ``revision``)."""

    count: int = Field(ge=1, le=8)
    implementer: str = Field(pattern=r"^0x[0-9a-f]{2}$")
    variant: str = Field(pattern=r"^0x[0-9a-f]$")
    part: str = Field(pattern=r"^0x[0-9a-f]{3}$")
    revision: int = Field(ge=0, le=15)


class CpuCluster(_Frozen):
    """The cores of one cpufreq policy: usually of one kind, not always (SM8550's 3-6)."""

    cores: tuple[CpuCores, ...] = Field(min_length=1)
    # ``scaling_available_frequencies``, ascending: its first and last are the
    # policy's ``cpuinfo_min_freq`` and ``cpuinfo_max_freq``.
    frequencies_khz: tuple[int, ...] = Field(min_length=1)

    @field_validator("frequencies_khz")
    @classmethod
    def _ascending(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if list(value) != sorted(set(value)) or value[0] <= 0:
            message = "frequencies must be positive and strictly ascending"
            raise ValueError(message)
        return value

    @property
    def count(self) -> int:
        return sum(kind.count for kind in self.cores)


class Soc(_Frozen):
    """A system-on-chip: ``ro.soc.*`` and its CPU clusters, little ones first."""

    model: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 _/-]*$")
    manufacturer: str = Field(pattern=r"^[A-Za-z0-9]+$")
    # The arch timer's rate as ``BogoMIPS`` prints it (``38.40`` is 19.2 MHz), and the
    # ``Features`` line: the kernel prints the same for every core.
    bogomips: str = Field(pattern=r"^\d+\.\d{2}$")
    features: str = Field(pattern=r"^[a-z0-9]+( [a-z0-9]+)*$")
    clusters: tuple[CpuCluster, ...] = Field(min_length=1)
    # Where the values were read: public dumps of that chip's cpuinfo, cpufreq and props.
    sources: tuple[Annotated[str, Field(pattern=r"^https://\S+$")], ...] = Field(min_length=1)

    @property
    def cores(self) -> int:
        return sum(cluster.count for cluster in self.clusters)


class OemBlueprint(_Frozen):
    brand: str = Field(pattern=r"^[A-Za-z0-9]+$")
    manufacturer: str = Field(pattern=r"^[A-Za-z0-9]+$")
    model: str = Field(pattern=r"^[A-Za-z0-9 -]+$")
    product: str = Field(pattern=r"^[A-Za-z0-9_]+$")
    device: str = Field(pattern=r"^[A-Za-z0-9_]+$")
    # ``ro.serialno``'s shape for the maker: Samsung ``R5C`` + 8 upper-case letters and
    # digits, Pixel 14 of them, Xiaomi and OnePlus 8 lower-case hex digits.
    serial_prefix: str = Field(pattern=r"^[A-Z0-9]*$")
    serial_length: int = Field(ge=8, le=20)
    serial_charset: Literal["alnum", "hex"]
    screen: Screen
    camera: Camera
    builds: dict[int, OemBuild] = Field(min_length=1)
    # The ``configs/soc`` file stem of its chip; ``None``: the host's CPUs, ``cpu_cores`` of them.
    soc: str | None = Field(default=None, pattern=r"^[a-z0-9_]+$")
    # ``Build.BOARD`` (``ro.product.board``): the phone's board as its vendor partition names it.
    board: str = Field(pattern=r"^[a-z0-9_]+$")
    # ``ro.hardware`` (what its bootloader passes as ``androidboot.hardware``: Pixels their
    # codename, Qualcomm phones ``qcom``) and ``ro.board.platform``, as its stock vendor
    # ``build.prop`` and device tree name them. One word each: the first goes on the
    # container's command line.
    hardware: str = Field(pattern=r"^[a-z0-9_]+$")
    platform: str = Field(pattern=r"^[a-z0-9_]+$")


@cache
def load_blueprints() -> tuple[OemBlueprint, ...]:
    """Every blueprint file, validated; read once per process."""
    return tuple(
        OemBlueprint.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(_BLUEPRINTS_DIR.glob("*.json"))
    )


def find_build(manufacturer: str, model: str, sdk: int) -> tuple[OemBlueprint, OemBuild] | None:
    """The phone ``manufacturer`` + ``model`` and its build for ``sdk``, or ``None``."""
    for blueprint in load_blueprints():
        if (blueprint.manufacturer, blueprint.model) == (manufacturer, model):
            build = blueprint.builds.get(sdk)
            return None if build is None else (blueprint, build)
    return None


@cache
def load_socs() -> dict[str, Soc]:
    """Every chip file by its stem, validated; read once per process."""
    return {
        path.stem: Soc.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(_SOC_DIR.glob("*.json"))
    }
