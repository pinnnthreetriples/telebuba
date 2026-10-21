"""Android's /data as overlayfs: an SDK's shared read-only base under each pod's own layer.

Opt-in (``EMULATOR__DATA_BASE_DIR`` and ``EMULATOR__DATA_DIFF_DIR``, docs/redroid.md):
redroid (``androidboot.use_redroid_overlayfs=1``) mounts ``/data`` as an overlay of
``/data-base`` (``<base>/<sdk>``, read-only, prepared by the operator with no phone's
identifiers in it) and ``/data-diff`` (``<diff>/<container>``), where everything the
phone writes lands. The diff outlives its container: a pod made again after its
gateway went keeps the account's sign-in. ``remove_diff`` deletes it with the pod.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
from core.emulator._errors import EmulatorError
from core.emulator._process import docker
from core.emulator.host import PROBE_IMAGE, ensure_probe_image

if TYPE_CHECKING:
    from pathlib import Path

    from schemas.emulator import EmulatorRefusalCode

_EMULATOR_COMMAND_FAILED: EmulatorRefusalCode = "emulator_command_failed"
_EMULATOR_DATA_BASE_MISSING: EmulatorRefusalCode = "emulator_data_base_missing"

# A container name (docker's own rule) is one plain path segment: never ``..`` or ``a/b``.
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

# Stdlib sink, names and codes only (see ``core.emulator._errors``).
logger = logging.getLogger(__name__)


class DataLayers(NamedTuple):
    """Host directories: mounted at ``/data-base`` (read-only) and ``/data-diff``."""

    base: Path
    diff: Path


def _segment(name: str) -> str:
    if not _NAME.fullmatch(name):
        raise EmulatorError(_EMULATOR_COMMAND_FAILED)
    return name


async def data_layers(name: str, sdk: int) -> DataLayers | None:
    """The container ``name``'s layers, its diff made when missing; ``None`` when off.

    A base the operator has not prepared for ``sdk`` is ``emulator_data_base_missing``.
    """
    base_root = settings.emulator.data_base_dir
    diff_root = settings.emulator.data_diff_dir
    if base_root is None or diff_root is None:
        return None
    layers = DataLayers(base_root / str(sdk), diff_root / _segment(name))
    if not await asyncio.to_thread(layers.base.is_dir):
        raise EmulatorError(_EMULATOR_DATA_BASE_MISSING)
    try:
        await asyncio.to_thread(layers.diff.mkdir, parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("the pod's data layer could not be made (%s)", type(exc).__name__)
        raise EmulatorError(_EMULATOR_COMMAND_FAILED) from exc
    return layers


async def remove_diff(name: str) -> None:
    """Delete the container ``name``'s diff, when shared /data is on.

    Android wrote its files as its own users (root, system, apps), so the app's user
    may not delete them: a throwaway container does, as root.
    """
    diff_root = settings.emulator.data_diff_dir
    if diff_root is None:
        return
    target = f"/data-diff/{_segment(name)}"
    await ensure_probe_image()
    await docker(
        "run",
        "--rm",
        "--volume",
        f"{diff_root.as_posix()}:/data-diff",
        "--entrypoint",
        "rm",
        PROBE_IMAGE,
        "-rf",
        target,
    )
