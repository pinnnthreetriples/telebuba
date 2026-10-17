"""The real phones a container can be, read from ``configs/oem_blueprints``.

One JSON per phone: who made it (brand, maker, model, product, device), how its
serial numbers look, the container's screen, its main camera, and one real build per Android SDK it
ran. A build is copied from a public firmware listing, never made up, and the
container claims exactly it. ``ro.build.fingerprint`` and ``ro.build.description``
are not stored: they are put together from these fields, so they cannot disagree.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ``configs/`` at the repository root, next to ``core/``.
CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs"
_BLUEPRINTS_DIR = CONFIGS_DIR / "oem_blueprints"


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
