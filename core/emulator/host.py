"""Whether this host can run a phone at all: docker, adb, and a kernel with binder.

What only the operator can fix, found before a phone is needed rather than at its
first boot: no docker (or its daemon down) is ``emulator_docker_unavailable``, no adb
``emulator_adb_missing``, and a kernel without binder (docs/redroid.md, Host setup)
``emulator_binder_missing``. Binder is read from ``/proc/filesystems`` in a throwaway
container of the gateway image, which shares the host's kernel; a kernel found
capable is not asked again while the process runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from core.emulator import _process
from core.emulator._errors import EmulatorError
from core.emulator.images import ensure_gateway_image, image_present

if TYPE_CHECKING:
    from schemas.emulator import EmulatorRefusalCode

_EMULATOR_ADB_MISSING: EmulatorRefusalCode = "emulator_adb_missing"
_EMULATOR_BINDER_MISSING: EmulatorRefusalCode = "emulator_binder_missing"
_EMULATOR_DOCKER_UNAVAILABLE: EmulatorRefusalCode = "emulator_docker_unavailable"

_binder_seen = False


async def check_host(*, build_probe: bool) -> None:
    """Refuse a host that cannot run a phone; the first code found wins.

    The binder probe needs the gateway image: ``build_probe`` builds it when missing
    (minutes); without it a host lacking the image is not probed yet.
    """
    try:
        await _process.docker("version", "--format", "{{.Server.Version}}")
    except EmulatorError as exc:
        raise EmulatorError(_EMULATOR_DOCKER_UNAVAILABLE) from exc
    try:
        await _process.adb_command("version", step="adb version")
    except EmulatorError as exc:
        raise EmulatorError(_EMULATOR_ADB_MISSING) from exc
    await _check_binder(build_probe=build_probe)


async def _check_binder(*, build_probe: bool) -> None:
    global _binder_seen  # noqa: PLW0603 - a process-wide fact about the host kernel
    if _binder_seen:
        return
    image = settings.emulator.gateway_image
    if build_probe:
        await ensure_gateway_image()
    elif not await image_present(image):
        return
    probe = ("run", "--rm", "--entrypoint", "grep", image, "-qw", "binder", "/proc/filesystems")
    if await _process.docker_or_none(*probe) is None:
        raise EmulatorError(_EMULATOR_BINDER_MISSING)
    _binder_seen = True
