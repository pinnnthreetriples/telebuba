"""Android's /data as overlayfs: an SDK's shared read-only base under each pod's own layer.

Opt-in (``EMULATOR__DATA_BASE_DIR`` and ``EMULATOR__DATA_DIFF_DIR``, docs/redroid.md):
redroid (``androidboot.use_redroid_overlayfs=1``) mounts ``/data`` as an overlay of
``/data-base`` (``<base>/<sdk>``, read-only, prepared by the operator with no phone's
identifiers in it) and ``/data-diff`` (``<diff>/<container>``), where everything the
phone writes lands. The diff outlives its container: a pod made again after its
gateway went keeps the account's sign-in. ``remove_diff`` deletes it with the pod: the
directory the container recorded at create (``DIFF_LABEL``) and the one configured now.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
from core.emulator._errors import EmulatorError
from core.emulator._process import docker
from core.emulator.host import PROBE_IMAGE, ensure_probe_image
from core.emulator.redroid import DIFF_LABEL

if TYPE_CHECKING:
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
    # Made by this call: a start that fails takes it away again.
    fresh: bool = False


def _segment(name: str) -> str:
    if not _NAME.fullmatch(name):
        raise EmulatorError(_EMULATOR_COMMAND_FAILED)
    return name


def _diff_parent(target: str, leaf: str) -> str:
    """The directory holding the diff ``target`` of ``leaf``; never a root, never ``..``.

    It is bind-mounted for ``rm -rf`` as root, so only a plain absolute path will do.
    """
    parent, _, last = target.rstrip("/").rpartition("/")
    parts = parent.split("/")
    if last != leaf or ".." in parts or "." in parts or not parent.strip("/"):
        raise EmulatorError(_EMULATOR_COMMAND_FAILED)
    if parent.rstrip("/").endswith(":"):  # a Windows drive's root
        raise EmulatorError(_EMULATOR_COMMAND_FAILED)
    return parent


async def data_layers(name: str, sdk: int, recorded: str | None = None) -> DataLayers | None:
    """The container ``name``'s layers, its diff made when missing; ``None`` when off.

    ``recorded`` is the diff of the Android this one replaces: it is kept, wherever
    ``EMULATOR__DATA_DIFF_DIR`` points now. A base the operator has not prepared for
    ``sdk`` is ``emulator_data_base_missing``.
    """
    base_root = settings.emulator.data_base_dir
    diff_root = settings.emulator.data_diff_dir
    if base_root is None or diff_root is None:
        return None
    leaf = _segment(name)
    diff = Path(_diff_parent(recorded, leaf)) / leaf if recorded else diff_root / leaf
    base = base_root / str(sdk)
    if not await asyncio.to_thread(base.is_dir):
        raise EmulatorError(_EMULATOR_DATA_BASE_MISSING)
    fresh = not await asyncio.to_thread(diff.exists)
    try:
        await asyncio.to_thread(diff.mkdir, parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("the pod's data layer could not be made (%s)", type(exc).__name__)
        raise EmulatorError(_EMULATOR_COMMAND_FAILED) from exc
    return DataLayers(base, diff, fresh=fresh)


async def recorded_diff(name: str) -> str | None:
    """The diff directory the Android container ``name`` was created on, if any."""
    label_format = f'{{{{index .Config.Labels "{DIFF_LABEL}"}}}}'
    label = await docker("inspect", "--format", label_format, name)
    return label.strip() or None


async def remove_diff(name: str, recorded: str | None = None) -> None:
    """Delete the container ``name``'s diff: ``recorded`` and the configured one.

    Android wrote its files as its own users (root, system, apps), so the app's user
    may not delete them: a throwaway container does, as root.
    """
    leaf = _segment(name)
    diff_root = settings.emulator.data_diff_dir
    targets = {recorded} if recorded else set()
    if diff_root is not None:
        targets.add(f"{diff_root.as_posix()}/{leaf}")
    for target in sorted(targets):
        parent = _diff_parent(target, leaf)
        await ensure_probe_image()
        await docker(
            "run",
            "--rm",
            "--volume",
            f"{parent}:/data-diff",
            "--entrypoint",
            "rm",
            PROBE_IMAGE,
            "-rf",
            f"/data-diff/{leaf}",
        )
