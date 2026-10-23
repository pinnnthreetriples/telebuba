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
from core.emulator._process import adb_command, docker
from core.emulator._profile import aosp_boot_properties
from core.emulator._topology import CPUINFO_RC, CPUINFO_STAGE, SYSFS_CPU, place_cpus
from core.emulator.telegram_app import forget_app

if TYPE_CHECKING:
    from core.emulator._data import DataLayers
    from core.emulator._profile import ContainerSpec
    from core.emulator.host import GpuMode
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
# Where an Android on shared /data keeps its diff, so deleting it finds that directory
# even after ``EMULATOR__DATA_DIFF_DIR`` changed.
DIFF_LABEL = "telebuba.data_diff"
ADB_PORT = 5555
_LIST_FORMAT = '{{.Label "telebuba.index"}}|{{.Names}}|{{.State}}'
# Both kinds of a pod in one listing: the last field is set on gateways only.
_POD_FORMAT = _LIST_FORMAT + '|{{.Label "telebuba.gateway"}}'
# Where the container's identity goes (see ``_give_identity``), after this mark.
_IDENTITY_FILE = "/product/etc/build.prop"
_IDENTITY_MARK = "# telebuba: the phone this container presents\n"
# redroid's switch for /data as the overlay of /data-base and /data-diff.
_OVERLAY_ARGUMENT = "androidboot.use_redroid_overlayfs=1"
# The image's entrypoint is ``/init qemu=1 androidboot.hardware=redroid``, and init keeps
# a boot argument's first value: ours replaces it, keeping the rest as it was.
_INIT = "/init"
_INIT_ARGUMENT = "qemu=1"

# Stdlib sink, names and codes only (see ``core.emulator._errors``).
logger = logging.getLogger(__name__)


class Instance(NamedTuple):
    index: int
    name: str
    running: bool
    # Frozen by ``docker pause`` between close visits (``core.emulator.pod``).
    paused: bool = False


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
            instances.append(Instance(int(index), name, state == "running", state == "paused"))
    return instances


async def list_containers(label: str) -> list[Instance]:
    """This app's containers carrying ``label`` (``ANDROID_LABEL`` or ``GATEWAY_LABEL``)."""
    return parse_list(
        await docker("ps", "-a", "--filter", f"label={label}", "--format", _LIST_FORMAT)
    )


async def list_pods() -> tuple[list[Instance], list[Instance]]:
    """This app's Android containers and gateways, from one ``docker ps``."""
    output = await docker("ps", "-a", "--filter", f"label={INDEX_LABEL}", "--format", _POD_FORMAT)
    androids: list[Instance] = []
    gateways: list[Instance] = []
    for line in output.splitlines():
        head, _, gateway = line.strip().rpartition("|")
        for instance in parse_list(head):
            (gateways if gateway else androids).append(instance)
    return androids, gateways


async def list_instances() -> list[Instance]:
    """Every Android container this app made, running or not."""
    return await list_containers(ANDROID_LABEL)


async def find_instance(name: str) -> Instance | None:
    return next((i for i in await list_instances() if i.name == name), None)


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
    """The product ``build.prop``: the image's own lines, then ``identity`` (ours replaced)."""
    own = image_prop.split(_IDENTITY_MARK, 1)[0]
    if own and not own.endswith("\n"):
        own += "\n"
    return own + _IDENTITY_MARK + identity


def _rewrite(path: Path, identity: str) -> None:
    text = path.read_text(encoding="utf-8")
    path.write_text(with_identity(text, identity), encoding="utf-8", newline="\n")


async def _give_identity(name: str, identity: str) -> None:
    """Write ``identity`` (a ``build_prop``) into the stopped container, before it boots.

    init reads the product partition's ``build.prop`` LAST of all, and a property read
    later wins over the same one read earlier: only there do ours beat the image's own
    ``ro.product.vendor.*`` / ``ro.vendor.build.*`` (read from ``/vendor``, after
    ``/system``). The image's lines are kept; our block after the mark replaces the one
    an earlier start wrote.
    """
    with tempfile.TemporaryDirectory(prefix="telebuba-redroid-") as scratch:
        local = Path(scratch) / "build.prop"
        await docker("cp", f"{name}:{_IDENTITY_FILE}", str(local))
        await asyncio.to_thread(_rewrite, local, identity)
        await docker("cp", str(local), f"{name}:{_IDENTITY_FILE}")


def image_for(spec: ContainerSpec) -> str:
    """The image configured for ``spec``'s SDK, or ``emulator_sdk_mismatch``."""
    image = settings.emulator.redroid_images.get(spec.sdk)
    if not image:
        raise EmulatorError(_EMULATOR_SDK_MISMATCH)
    return image


def _volumes(data: DataLayers) -> tuple[str, ...]:
    """The ``docker create`` options that mount ``data`` where redroid looks for it."""
    return (
        "--volume",
        f"{data.base.as_posix()}:/data-base:ro",
        "--volume",
        f"{data.diff.as_posix()}:/data-diff",
    )


async def create_instance(  # noqa: PLR0913 - one keyword per thing the pod decides
    name: str,
    spec: ContainerSpec,
    *,
    index: int,
    gateway: str,
    gpu: GpuMode = "guest",
    data: DataLayers | None = None,
    kept_diff: str | None = None,
) -> Instance:
    """A new, stopped container named ``name`` presenting ``spec``'s phone.

    It takes the pod's ``index`` and joins the network namespace of the container
    named ``gateway``. The image is the one configured for ``spec``'s SDK; an SDK
    without one is refused before anything is created. The phone goes in through
    ``build_prop``; its screen, serial number and ``ro.hardware`` on the command line
    (none has a space), the image's entrypoint replaced since it names redroid. It
    draws in software (``guest``) or on the host's GPU (``host``: given ``/dev/dri``),
    as ``core.emulator.host.gpu_mode`` resolved it. With ``data`` its /data is the
    overlay of those layers (``core.emulator._data``), else its own;
    ``kept_diff`` is the diff of the Android it replaces, still labelled for deletion.
    Its chip's CPUs go in through ``_cpu_options`` and the init script that binds
    its cpuinfo.
    """
    image = image_for(spec)
    device = ("--device", "/dev/dri:/dev/dri") if gpu == "host" else ()
    # Where its diff is, for deletion; with shared /data off now, the one it replaces keeps.
    diff = data.diff.as_posix() if data else kept_diff
    mounts = _volumes(data) if data else ()
    if diff:
        mounts = (*mounts, "--label", f"{DIFF_LABEL}={diff}")
    overlay = (_OVERLAY_ARGUMENT,) if data is not None else ()
    identity = build_prop(spec)  # refused before anything is created
    cpus, topology = await _cpu_options(spec, index)
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
        *cpus,
        "--memory",
        f"{settings.emulator.memory_mb}m",
        *device,
        *mounts,
        "--entrypoint",
        _INIT,
        image,
        _INIT_ARGUMENT,
        f"androidboot.hardware={spec.hardware}",
        f"androidboot.redroid_width={spec.screen.width}",
        f"androidboot.redroid_height={spec.screen.height}",
        f"androidboot.redroid_dpi={spec.screen.dpi}",
        f"androidboot.serialno={spec.serial}",
        "androidboot.use_memfd=true",
        f"androidboot.redroid_gpu_mode={gpu}",
        *overlay,
    )
    if topology is not None:
        await docker("cp", str(topology / "cpuinfo.rc"), f"{name}:{CPUINFO_RC}")
    await _give_identity(name, identity)
    return Instance(index, name, running=False)


async def _cpu_options(spec: ContainerSpec, index: int) -> tuple[list[str], Path | None]:
    """``docker create``'s CPU options for ``spec`` and its topology directory, if any.

    A phone with a chip is pinned to the pod's cores and sees the chip's topology
    (``core.emulator._topology``); one without gets ``cpu_cores`` of the host's.
    """
    if spec.soc is None:
        return ["--cpus", str(settings.emulator.cpu_cores)], None
    placement = await place_cpus(spec.soc, index)
    directory = placement.directory
    return [
        "--cpuset-cpus",
        placement.cpuset,
        "--volume",
        f"{directory / 'cpu'}:{SYSFS_CPU}:ro",
        "--volume",
        f"{directory / 'cpuinfo'}:{CPUINFO_STAGE}:ro",
    ], directory


async def refresh_identity(name: str, spec: ContainerSpec, index: int) -> None:
    """Rewrite a stopped container's ``build_prop`` before it boots again.

    The phone is the same (its serial and IMEI follow the account id), but the
    account's proxy may have changed country since, and the timezone and carrier
    follow it. A phone with a chip is pinned to the pod's cores again, its quota
    raised to all of them (one made before kept ``--cpus cpu_cores``, and ``--cpus 0``
    does not clear it); its mounts are a new container's only (one made before has
    none), nothing is made again.
    """
    await _give_identity(name, build_prop(spec))
    if spec.soc is not None:
        placement = await place_cpus(spec.soc, index)
        await docker(
            "update", "--cpus", str(placement.cores), "--cpuset-cpus", placement.cpuset, name
        )


async def launch_instance(name: str, index: int) -> None:
    """Start the container ``name``; adb forgets its old connection to ``index``'s port.

    A connection kept from the container's previous run stays ``offline`` and is
    never retried by itself; ``adb.wait_for_boot`` connects afresh. The Telegram known
    at that serial is forgotten too: the container may be a new one at the same index.
    """
    forget_app(adb_serial(index))
    await docker("start", name)
    with suppress(EmulatorError):
        await adb_command("disconnect", adb_serial(index), step="adb disconnect")


async def show_instance(name: str, index: int) -> None:
    """Open the container's scrcpy window, unless this process has one open already.

    Nothing opens while ``desktop_viewer`` is off: the window encodes video for as long
    as it stays open, and the web screen (``core.emulator.scrcpy``) does it on demand.
    """
    if not settings.emulator.desktop_viewer or viewer_open(name):
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


def viewer_open(name: str) -> bool:
    """Whether this process's scrcpy window of the container ``name`` is still open."""
    viewer = _VIEWERS.get(name)
    return viewer is not None and viewer.returncode is None


async def close_viewer(name: str) -> None:
    viewer = _VIEWERS.pop(name, None)
    if viewer is not None and viewer.returncode is None:
        with suppress(ProcessLookupError):
            viewer.kill()
        await asyncio.shield(viewer.wait())
