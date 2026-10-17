"""redroid under Docker: find, create and start Android containers; show one in scrcpy.

A container is found again by its NAME (prefix + account id) and has an INDEX, kept
in a docker label, from which its adb serial follows (``127.0.0.1:<first port + N>``,
published on loopback only by its pod's gateway: the Android container joins that
container's network namespace and has no ports of its own, see ``core.emulator.pod``).
Docker has no window, so ``show_instance`` opens a scrcpy one.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
from core.emulator._errors import EmulatorError
from core.emulator._process import docker, run_command

if TYPE_CHECKING:
    from core.emulator._profile import ContainerSpec
    from schemas.emulator import EmulatorRefusalCode

_EMULATOR_COMMAND_FAILED: EmulatorRefusalCode = "emulator_command_failed"
_EMULATOR_DEVICE_UNKNOWN: EmulatorRefusalCode = "emulator_device_unknown"
_EMULATOR_NOT_FOUND: EmulatorRefusalCode = "emulator_not_found"
_EMULATOR_SDK_MISMATCH: EmulatorRefusalCode = "emulator_sdk_mismatch"

# Every Android container this app made carries ANDROID_LABEL, every gateway
# GATEWAY_LABEL; both carry the pod's index, which decides the adb port.
ANDROID_LABEL = "telebuba.emulator"
GATEWAY_LABEL = "telebuba.gateway"
INDEX_LABEL = "telebuba.index"
ADB_PORT = 5555
_LIST_FORMAT = '{{.Label "telebuba.index"}}|{{.Names}}|{{.State}}'
# Where the container's identity goes: init reads ``/system_dlkm/etc/build.prop``.
_IDENTITY_DIR = "/system_dlkm/"

# Stdlib sink, names and codes only (see ``core.emulator._errors``).
logger = logging.getLogger(__name__)


class Instance(NamedTuple):
    index: int
    name: str
    running: bool


# One scrcpy window per container, process-local like the rest of the runtime state.
_VIEWERS: dict[str, asyncio.subprocess.Process] = {}


def adb_serial(index: int) -> str:
    return f"127.0.0.1:{settings.emulator.redroid_first_adb_port + index}"


def parse_list(output: str) -> list[Instance]:
    """``docker ps`` lines (``index|name|state``) of this app's containers."""
    instances: list[Instance] = []
    for line in output.splitlines():
        index, _, rest = line.strip().partition("|")
        name, _, state = rest.partition("|")
        if index.isdigit() and name:
            instances.append(Instance(int(index), name, state == "running"))
    return instances


async def list_containers(label: str) -> list[Instance]:
    """This app's containers carrying ``label`` (``ANDROID_LABEL`` or ``GATEWAY_LABEL``)."""
    return parse_list(
        await docker("ps", "-a", "--filter", f"label={label}", "--format", _LIST_FORMAT)
    )


async def _list() -> list[Instance]:
    return await list_containers(ANDROID_LABEL)


async def find_instance(name: str) -> Instance | None:
    return next((i for i in await _list() if i.name == name), None)


async def _name_of(index: int) -> str:
    instance = next((i for i in await _list() if i.index == index), None)
    if instance is None:
        raise EmulatorError(_EMULATOR_COMMAND_FAILED)
    return instance.name


def build_prop(spec: ContainerSpec) -> str:
    """The ``build.prop`` that makes Android report ``spec``'s maker and model.

    ``Build.MANUFACTURER`` / ``Build.MODEL`` read these two properties; set here, they
    win over the image's own (``redroid``). They cannot go on the container's command
    line: redroid's first-stage init re-splits it on spaces (``Pixel 7`` -> ``Pixel``).
    """
    lines = (f"ro.product.manufacturer={spec.manufacturer}", f"ro.product.model={spec.model}")
    if any(char in line for line in lines for char in "\r\n"):
        raise EmulatorError(_EMULATOR_DEVICE_UNKNOWN)
    return "\n".join(lines) + "\n"


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


async def _give_identity(name: str, identity: str) -> None:
    """Copy ``identity`` (a ``build_prop``) into the stopped container, before its first boot.

    ``/system_dlkm/etc/build.prop`` is read by init at boot and is absent from the
    images, so nothing of the image's is replaced.
    """
    with tempfile.TemporaryDirectory(prefix="telebuba-redroid-") as scratch:
        etc = Path(scratch) / "etc"
        await asyncio.to_thread(_write_text, etc / "build.prop", identity)
        await docker("cp", str(etc), f"{name}:{_IDENTITY_DIR}")


def image_for(spec: ContainerSpec) -> str:
    """The image configured for ``spec``'s SDK, or ``emulator_sdk_mismatch``."""
    image = settings.emulator.redroid_images.get(spec.sdk)
    if not image:
        raise EmulatorError(_EMULATOR_SDK_MISMATCH)
    return image


async def create_instance(name: str, spec: ContainerSpec, *, index: int, gateway: str) -> Instance:
    """A new, stopped container named ``name`` presenting ``spec``'s phone.

    It takes the pod's ``index`` and joins the network namespace of the container
    named ``gateway``. The image is the one configured for ``spec``'s SDK; an SDK
    without one is refused before anything is created. Maker and model go in through
    ``build_prop``. redroid has no telephony, so ``spec.imei`` has nowhere to go.
    """
    image = image_for(spec)
    identity = build_prop(spec)  # refused before anything is created
    width, height, dpi = settings.emulator.resolution.split(",")
    await docker(
        "create",
        "--name",
        name,
        "--label",
        f"{ANDROID_LABEL}=1",
        "--label",
        f"{INDEX_LABEL}={index}",
        "--privileged",
        "--network",
        f"container:{gateway}",
        "--cpus",
        str(settings.emulator.cpu_cores),
        "--memory",
        f"{settings.emulator.memory_mb}m",
        image,
        f"androidboot.redroid_width={width}",
        f"androidboot.redroid_height={height}",
        f"androidboot.redroid_dpi={dpi}",
        "androidboot.use_memfd=true",
    )
    await _give_identity(name, identity)
    return Instance(index, name, running=False)


async def launch_instance(index: int) -> None:
    """Start the container; adb forgets its old connection to the port first.

    A connection kept from the container's previous run stays ``offline`` and is
    never retried by itself; ``adb.wait_for_boot`` connects afresh.
    """
    await docker("start", await _name_of(index))
    with suppress(EmulatorError):
        await run_command(
            [str(settings.emulator.adb_path), "disconnect", adb_serial(index)],
            seconds=settings.emulator.command_timeout_seconds,
            step="adb disconnect",
        )


async def show_instance(index: int) -> None:
    """Open the container's scrcpy window, unless this process has one open already."""
    name = await _name_of(index)
    viewer = _VIEWERS.get(name)
    if viewer is not None and viewer.returncode is None:
        return
    argv = [
        str(settings.emulator.scrcpy_path),
        "--serial",
        adb_serial(index),
        "--window-title",
        name,
        "--no-audio",
    ]
    try:
        _VIEWERS[name] = await asyncio.create_subprocess_exec(  # nosec B603 - argv list, no shell
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise EmulatorError(_EMULATOR_NOT_FOUND) from exc
    except OSError as exc:
        logger.warning("scrcpy could not start (%s)", type(exc).__name__)
        raise EmulatorError(_EMULATOR_COMMAND_FAILED) from exc


async def close_viewer(name: str) -> None:
    viewer = _VIEWERS.pop(name, None)
    if viewer is not None and viewer.returncode is None:
        with suppress(ProcessLookupError):
            viewer.kill()
        await asyncio.shield(viewer.wait())
