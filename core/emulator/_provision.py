"""What a stopped redroid container is given before it boots, as the image's own files.

Its identity (``build.prop``), feature files, network overlay, sensors HAL and Google
apps (microG) go in as tars (``core.emulator._archive``) with AOSP's owners and modes;
its battery is a ``power_supply`` tree on the host, mounted by ``core.emulator.redroid``.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from core.config import settings
from core.emulator._archive import SHELL, copy_in, pack, read_tree
from core.emulator._battery import generate_initial_battery_state, write_power_supply
from core.emulator._errors import EmulatorError
from core.emulator._features import PERMISSIONS_DIR, write_permission_files
from core.emulator._microg import PROBE, product_files
from core.emulator._process import docker, docker_or_none
from core.emulator._profile import aosp_boot_properties
from core.emulator._sensors import (
    HAL_NAME,
    SENSORS_RC,
    hal_binary,
    host_abi,
    write_disabled_tree,
    write_vendor_tree,
)
from core.emulator._transport import write_overlay_tree
from core.emulator.assets import ensure_microg

if TYPE_CHECKING:
    from core.emulator._profile import ContainerSpec
    from schemas.emulator import EmulatorRefusalCode

_EMULATOR_DEVICE_UNKNOWN: EmulatorRefusalCode = "emulator_device_unknown"
# Stdlib sink, step names only (see ``core.emulator._errors``).
logger = logging.getLogger(__name__)
# Where the container's identity goes (see ``give_identity``).
_IDENTITY_FILE = PurePosixPath("/product/etc/build.prop")
# What earlier versions wrote before their identity block: dropped with that block.
_LEGACY_MARK = "# telebuba: the phone this container presents\n"
# Ours though an identity may leave them out (no carrier, no chip); never the image's.
_OWN_PREFIXES = ("gsm.", "ro.soc.")
# /vendor's directories that are not listable, as the image has them (``_archive``).
_VENDOR_DIRECTORIES = {"bin": 0o751, "bin/hw": 0o751}


def build_prop(spec: ContainerSpec) -> str:
    """The ``build.prop`` that makes Android report ``spec``'s phone, firmware and region.

    ``Build.*`` read these properties (``core.emulator._profile.aosp_boot_properties``);
    set here, they win over the image's own (``redroid``). They cannot go on the
    container's command line: redroid's first-stage init re-splits it on spaces
    (``Pixel 7`` -> ``Pixel``).
    """
    lines = [f"{key}={value}" for key, value in aosp_boot_properties(spec).items()]
    if any(char in line for line in lines for char in "\r\n"):
        raise EmulatorError(_EMULATOR_DEVICE_UNKNOWN)
    return "\n".join(lines) + "\n"


def with_identity(image_prop: str, identity: str) -> str:
    """The product ``build.prop``: the image's lines but those ``identity`` sets, then it.

    Ours from an earlier start go as well: a line ``identity`` sets again, a carrier's
    or chip's (``_OWN_PREFIXES``), and an older version's whole block after its mark.
    AOSP's comments and the image's other lines stay; nothing of ours is a comment.
    """
    keys = {line.partition("=")[0] for line in identity.splitlines()}
    own = image_prop.split(_LEGACY_MARK, 1)[0].splitlines()
    return "".join(f"{line}\n" for line in own if not _ours(line, keys)) + identity


def _ours(line: str, keys: set[str]) -> bool:
    key, equals, _value = line.partition("=")
    key = key.strip()
    if not equals or key.startswith("#"):
        return False
    return key in keys or key.startswith(_OWN_PREFIXES)


async def give_identity(name: str, identity: str) -> None:
    """Write ``identity`` (a ``build_prop``) into the stopped container, before it boots.

    init reads the product partition's ``build.prop`` LAST of all, and a property read
    later wins over the same one read earlier: only there do ours beat the image's own
    ``ro.product.vendor.*`` / ``ro.vendor.build.*`` (read from ``/vendor``, after
    ``/system``). The image's other lines are kept, ours replace the ones an earlier
    start wrote, and the file goes back as the image's is: root's, ``0644``.
    """
    with tempfile.TemporaryDirectory(prefix="telebuba-redroid-") as scratch:
        local = Path(scratch) / _IDENTITY_FILE.name
        await docker("cp", f"{name}:{_IDENTITY_FILE}", str(local))
        image_prop = await asyncio.to_thread(local.read_text, encoding="utf-8")
    prop = with_identity(image_prop, identity).encode("utf-8")
    await copy_in(name, str(_IDENTITY_FILE.parent), pack({_IDENTITY_FILE.name: prop}))


async def give_features(name: str) -> None:
    """Copy the phone's feature files (``core.emulator._features``) into the stopped container.

    One ``docker cp`` of a directory's contents: they replace the image's files of the
    same name and leave the others alone. Idempotent, so every refresh does it again.
    """
    with tempfile.TemporaryDirectory(prefix="telebuba-features-") as scratch:
        await asyncio.to_thread(write_permission_files, Path(scratch))
        files = await asyncio.to_thread(read_tree, Path(scratch))
    await copy_in(name, PERMISSIONS_DIR, pack(files))


async def give_transport(name: str) -> None:
    """Copy the network overlay (``core.emulator._transport``) into the stopped container.

    One ``docker cp`` into /product, whose ``overlay`` directory PackageManager scans at
    every boot. The configured transport is written every time, ``ethernet`` included,
    so a refresh turns an earlier label back.
    """
    with tempfile.TemporaryDirectory(prefix="telebuba-transport-") as scratch:
        transport = settings.emulator.network_transport
        await asyncio.to_thread(write_overlay_tree, Path(scratch), transport)
        files = await asyncio.to_thread(read_tree, Path(scratch))
    await copy_in(name, "/product", pack(files))


async def give_sensors(name: str, spec: ContainerSpec, *, refresh: bool = False) -> None:
    """Copy the sensors HAL (``core.emulator._sensors``) into the stopped container's /vendor.

    One ``docker cp`` of a directory's contents: the image has none of these files, and
    a refresh replaces its own. Idempotent, so every refresh does it again. While it is
    off, not built for the host, or the phone has no sensor sheet, a refresh turns off a
    HAL an earlier start installed (``write_disabled_tree``); a container without one,
    or a new one, is left alone.
    """
    binary = await hal_binary()
    with tempfile.TemporaryDirectory(prefix="telebuba-sensors-") as scratch:
        tree = Path(scratch) / "vendor"
        if binary is not None and await asyncio.to_thread(write_vendor_tree, tree, spec, binary):
            await give_vendor(name, tree)
        elif refresh and await _has_sensors(name, Path(scratch) / "rc"):
            await asyncio.to_thread(write_disabled_tree, tree)
            await give_vendor(name, tree)


async def give_vendor(name: str, tree: Path) -> None:
    """Copy ``tree`` into /vendor as the image's own: root:shell, the HAL executable."""
    files = await asyncio.to_thread(read_tree, tree)
    archive = pack(
        files,
        group=SHELL,
        executables={f"bin/hw/{HAL_NAME}"},
        directories=_VENDOR_DIRECTORIES,
    )
    await copy_in(name, "/vendor", archive)


async def _has_sensors(name: str, scratch: Path) -> bool:
    """Whether the stopped container has the HAL's init script (copied out to ``scratch``)."""
    return await docker_or_none("cp", f"{name}:{SENSORS_RC}", str(scratch)) is not None


async def give_microg(name: str, *, refresh: bool = False) -> None:
    """Copy microG (``core.emulator._microg``) into the stopped container's /product.

    One ``docker cp`` of its three APKs and GmsCore's libraries for the host's ABI,
    which PackageManager installs as system apps at the next boot. A container keeps
    what it was given: a refresh copies it only into one without it (made before, or
    whose download failed). Off (``microg_dir`` empty) or on a host with no ABI of
    ours, nothing is copied; a download that fails leaves the phone without it.
    """
    if settings.emulator.microg_dir is None or (abi := await host_abi()) is None:
        return
    if refresh:
        with tempfile.TemporaryDirectory(prefix="telebuba-microg-") as scratch:
            probe = await docker_or_none("cp", f"{name}:{PROBE}", str(Path(scratch) / "probe"))
        if probe is not None:
            return
    try:
        apks = await ensure_microg()
    except EmulatorError as exc:
        logger.warning("microG not fetched (%s): the phone boots without it", exc.code)
        return
    files = await asyncio.to_thread(product_files, apks, abi)
    await copy_in(name, "/product", await asyncio.to_thread(pack, files))


async def give_battery(spec: ContainerSpec, index: int) -> Path:
    """Write pod ``index``'s ``power_supply`` tree: the battery its account has now.

    One directory per pod index (``battery_dir``), mounted over ``POWER_SUPPLY`` by
    ``create_instance`` and written again before each boot, so the health HAL starts
    from the level of the phone's hour (its timezone), with a voltage to match.
    """
    now = datetime.now(ZoneInfo(spec.region.timezone))
    state = generate_initial_battery_state(spec.account_id, now)
    directory = settings.emulator.battery_dir.resolve() / str(index)
    await asyncio.to_thread(write_power_supply, directory, state)
    return directory
