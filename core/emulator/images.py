"""The Docker images a pod runs, made present before its first container is created.

- Android: the redroid image ``EMULATOR__REDROID_IMAGES`` (``_ARM64`` on an ARM64 host)
  names for the phone's SDK, pulled from Docker Hub;
- the net-gateway: ``EMULATOR__GATEWAY_IMAGE``, built from ``docker/net-gateway``,
  whose Dockerfile pins and checks the one file it downloads.

An image already present is used as it is: nothing is pulled or rebuilt behind the
operator's back. A pull or build is bounded by ``image_timeout_seconds`` and fails as
``emulator_image_unavailable``; concurrent callers wait for the one in flight. A pod's
two images are made present side by side: a build need not wait for a pull, and a
failure of either ends both.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from core.config import settings
from core.emulator import _process
from core.emulator._errors import EmulatorError
from core.emulator.redroid import image_for

if TYPE_CHECKING:
    from core.emulator._profile import ContainerSpec
    from schemas.emulator import EmulatorRefusalCode

_EMULATOR_COMMAND_FAILED: EmulatorRefusalCode = "emulator_command_failed"
_EMULATOR_COMMAND_TIMEOUT: EmulatorRefusalCode = "emulator_command_timeout"
_EMULATOR_IMAGE_UNAVAILABLE: EmulatorRefusalCode = "emulator_image_unavailable"

GATEWAY_CONTEXT = Path(__file__).resolve().parents[2] / "docker" / "net-gateway"

# Stdlib sink, step names only (see ``core.emulator._errors``).
logger = logging.getLogger(__name__)

_LOCKS: dict[str, asyncio.Lock] = {}


async def ensure_pod_images(spec: ContainerSpec) -> str | None:
    """Make the gateway image and ``spec``'s Android image present; the gateway image's ID.

    Both at once. The first failure is raised at once: the other is cancelled, its pull
    or build killed (``_process.run_command``) and its lock let go. The ID is what a
    pod's gateway is compared with (``core.emulator.pod``), so it is not inspected twice;
    ``None`` when the image was just built (the comparison then reads it itself).
    """
    gateway = asyncio.ensure_future(ensure_gateway_image())
    android = asyncio.ensure_future(_ensure(await image_for(spec), ("pull", "{image}")))
    try:
        await asyncio.wait((gateway, android), return_when=asyncio.FIRST_EXCEPTION)
    finally:
        for task in (gateway, android):
            task.cancel()
        await asyncio.gather(gateway, android, return_exceptions=True)
    for task in (gateway, android):
        if not task.cancelled() and (failure := task.exception()) is not None:
            raise failure
    return gateway.result()


async def ensure_gateway_image() -> str | None:
    return await _ensure(
        settings.emulator.gateway_image, ("build", "-t", "{image}", str(GATEWAY_CONTEXT))
    )


async def ensure_pulled(image: str, *, seconds: float) -> None:
    """Pull ``image`` when missing, bounded by ``seconds`` instead of the usual timeout."""
    await _ensure(image, ("pull", "{image}"), seconds=seconds)


async def image_present(image: str) -> bool:
    return await _image_id(image) is not None


async def _image_id(image: str) -> str | None:
    listed = await _process.docker_or_none("image", "inspect", "--format", "{{.Id}}", image)
    return None if listed is None else listed.strip()


async def _ensure(image: str, make: tuple[str, ...], *, seconds: float | None = None) -> str | None:
    """``image``'s ID when it was present; ``None`` once it was pulled or built."""
    async with _LOCKS.setdefault(image, asyncio.Lock()):
        if (present := await _image_id(image)) is not None:
            return present
        argv = [str(settings.emulator.docker_path), *(a.format(image=image) for a in make)]
        try:
            await _process.run_command(
                argv,
                seconds=seconds or settings.emulator.image_timeout_seconds,
                step=f"docker {make[0]}",
            )
        except EmulatorError as exc:
            if exc.code in {_EMULATOR_COMMAND_FAILED, _EMULATOR_COMMAND_TIMEOUT}:
                raise EmulatorError(_EMULATOR_IMAGE_UNAVAILABLE) from exc
            raise
    logger.info("emulator image ready after docker %s", make[0])
    return None
