"""Whether this host can run a phone at all: docker, adb, and a kernel with binder.

What only the operator can fix, found before a phone is needed rather than at its
first boot: no docker (or its daemon down) is ``emulator_docker_unavailable``, no adb
``emulator_adb_missing``, and a kernel without binder (docs/redroid.md, Host setup)
``emulator_binder_missing``. Binder is read from ``/proc/filesystems`` in a throwaway
container, which shares the host's kernel, of the gateway's own base image
(``PROBE_IMAGE``, a few MB, pulled when missing within ``_PROBE_PULL_SECONDS``), so
even a host that never ran a phone is probed at the first Start. A kernel found
capable is not asked again while the process runs.

The GPU (``EMULATOR__GPU_MODE``) is probed the same way, a throwaway container given
``/dev/dri``: ``host`` without one is ``emulator_gpu_missing`` (at the first Start too),
``auto`` without one draws in software (``guest``). A GPU found is not asked again.

The Docker host's core count (``EMULATOR__CPUSET_CORES``, ``core.emulator._cpuset``)
is asked of docker itself, not of this machine (which may be Windows next to WSL2),
once per process.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from core.config import settings
from core.emulator import _process
from core.emulator._errors import EmulatorError
from core.emulator.images import ensure_pulled

if TYPE_CHECKING:
    from schemas.emulator import EmulatorRefusalCode

_EMULATOR_ADB_MISSING: EmulatorRefusalCode = "emulator_adb_missing"
_EMULATOR_BINDER_MISSING: EmulatorRefusalCode = "emulator_binder_missing"
_EMULATOR_COMMAND_FAILED: EmulatorRefusalCode = "emulator_command_failed"
_EMULATOR_DOCKER_UNAVAILABLE: EmulatorRefusalCode = "emulator_docker_unavailable"
_EMULATOR_GPU_MISSING: EmulatorRefusalCode = "emulator_gpu_missing"

# What a new Android container is created with: ``auto`` resolved to one of these.
GpuMode = Literal["guest", "host"]
DRI_DEVICE = "/dev/dri"

# The FROM of docker/net-gateway/Dockerfile: its layer is reused by the gateway build.
PROBE_IMAGE = "alpine:3.20"
# A Start waits for this pull; a few MB, so a longer wait means no usable network.
_PROBE_PULL_SECONDS = 120.0

_binder_seen = False
_dri_seen = False
_cpus_seen = 0


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
    if settings.emulator.gpu_mode == "host":
        await gpu_mode()


async def gpu_mode() -> GpuMode:
    """How a new Android container draws, ``EMULATOR__GPU_MODE`` checked against the host."""
    mode = settings.emulator.gpu_mode
    if mode == "guest":
        return "guest"
    if await _has_dri():
        return "host"
    if mode == "host":
        raise EmulatorError(_EMULATOR_GPU_MISSING)
    return "guest"


async def host_cpus() -> int:
    """How many cores the Docker host has; an answer that is not a count is a failed command."""
    global _cpus_seen  # noqa: PLW0603 - a process-wide fact about the host's cores
    if not _cpus_seen:
        answer = (await _process.docker("info", "--format", "{{.NCPU}}")).strip()
        if not answer.isdigit() or int(answer) < 1:
            raise EmulatorError(_EMULATOR_COMMAND_FAILED)
        _cpus_seen = int(answer)
    return _cpus_seen


async def _check_binder() -> None:
    global _binder_seen  # noqa: PLW0603 - a process-wide fact about the host kernel
    if _binder_seen:
        return
    if not await _probe("--entrypoint", "grep", PROBE_IMAGE, "-qw", "binder", "/proc/filesystems"):
        raise EmulatorError(_EMULATOR_BINDER_MISSING)
    _binder_seen = True


async def _has_dri() -> bool:
    global _dri_seen  # noqa: PLW0603 - a process-wide fact about the host's devices
    if not _dri_seen:
        device = f"{DRI_DEVICE}:{DRI_DEVICE}"
        _dri_seen = await _probe("--device", device, "--entrypoint", "true", PROBE_IMAGE)
    return _dri_seen


async def ensure_probe_image() -> None:
    """``PROBE_IMAGE`` present, pulled within ``_PROBE_PULL_SECONDS`` when missing."""
    await ensure_pulled(PROBE_IMAGE, seconds=_PROBE_PULL_SECONDS)


async def _probe(*argv: str) -> bool:
    """Whether a throwaway ``docker run --rm`` of ``argv`` succeeds."""
    await ensure_probe_image()
    return await _process.docker_or_none("run", "--rm", *argv) is not None
