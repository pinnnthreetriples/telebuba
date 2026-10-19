"""Whether this host can run a phone at all: docker, adb, and a kernel with binder.

What only the operator can fix, found before a phone is needed rather than at its
first boot: no docker (or its daemon down) is ``emulator_docker_unavailable``, no adb
``emulator_adb_missing``, and a kernel without binder (docs/redroid.md, Host setup)
``emulator_binder_missing``. Binder is read from ``/proc/filesystems`` in a throwaway
container, which shares the host's kernel, of the gateway's own base image
(``PROBE_IMAGE``, a few MB, pulled when missing within ``_PROBE_PULL_SECONDS``), so
even a host that never ran a phone is probed at the first Start. A kernel found
capable is not asked again while the process runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.emulator import _process
from core.emulator._errors import EmulatorError
from core.emulator.images import ensure_pulled

if TYPE_CHECKING:
    from schemas.emulator import EmulatorRefusalCode

_EMULATOR_ADB_MISSING: EmulatorRefusalCode = "emulator_adb_missing"
_EMULATOR_BINDER_MISSING: EmulatorRefusalCode = "emulator_binder_missing"
_EMULATOR_DOCKER_UNAVAILABLE: EmulatorRefusalCode = "emulator_docker_unavailable"

# The FROM of docker/net-gateway/Dockerfile: its layer is reused by the gateway build.
PROBE_IMAGE = "alpine:3.20"
# A Start waits for this pull; a few MB, so a longer wait means no usable network.
_PROBE_PULL_SECONDS = 120.0

_binder_seen = False


async def check_host() -> None:
    """Refuse a host that cannot run a phone; the first code found wins."""
    try:
        await _process.docker("version", "--format", "{{.Server.Version}}")
    except EmulatorError as exc:
        raise EmulatorError(_EMULATOR_DOCKER_UNAVAILABLE) from exc
    try:
        await _process.adb_command("version", step="adb version")
    except EmulatorError as exc:
        raise EmulatorError(_EMULATOR_ADB_MISSING) from exc
    await _check_binder()


async def _check_binder() -> None:
    global _binder_seen  # noqa: PLW0603 - a process-wide fact about the host kernel
    if _binder_seen:
        return
    await ensure_pulled(PROBE_IMAGE, seconds=_PROBE_PULL_SECONDS)
    probe = (
        "run",
        "--rm",
        "--entrypoint",
        "grep",
        PROBE_IMAGE,
        "-qw",
        "binder",
        "/proc/filesystems",
    )
    if await _process.docker_or_none(*probe) is None:
        raise EmulatorError(_EMULATOR_BINDER_MISSING)
    _binder_seen = True
