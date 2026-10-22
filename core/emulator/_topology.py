"""The phone's CPUs inside its container: how many, which host cores, what they look like.

Without a chip (``ContainerSpec.soc``) a container gets ``cpu_cores`` of the host's CPUs
and sees the host's own. With one it is pinned to as many host cores as the chip has
(``--cpuset-cpus``, fewer on a smaller host) and sees that chip:

* ``/sys/devices/system/cpu`` is replaced whole by a directory written here: WSL2's
  sysfs has no ``cpufreq/`` to mount single files onto, and Android's init leaves
  /sys alone.
* ``/proc/cpuinfo`` cannot be mounted by Docker: init mounts a fresh proc over Docker's
  and hides it. The file is mounted at ``CPUINFO_STAGE`` instead, and an init script
  (``CPUINFO_RC``, copied into the image's ``/vendor/etc/init``) binds it over
  /proc/cpuinfo at every boot, before zygote; app namespaces follow init's.

The files are made from ``configs/soc`` in one host directory per chip and core count,
shared by every container of that chip, and written only where they differ. Core N of
the topology is the chip's core N, little ones first; ``scaling_cur_freq`` stays put.
Their layout follows a real phone's (pytorch/cpuinfo's Pixel 8 mock): one
``physical_package_id`` per cluster, each core its own sibling, ``kernel_max`` 31.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
from core.emulator._blueprints import load_socs
from core.emulator._process import docker

if TYPE_CHECKING:
    from pathlib import Path

    from core.emulator._blueprints import CpuCluster, CpuCores, Soc

CPUINFO_STAGE = "/dev/cpuinfo"
CPUINFO_RC = "/vendor/etc/init/cpuinfo.rc"
SYSFS_CPU = "/sys/devices/system/cpu"
_RC_TEXT = (
    "on early-init\n"
    f"    mount none {CPUINFO_STAGE} /proc/cpuinfo bind\n"
    f"    umount {CPUINFO_STAGE}\n"
)
# GKI arm64 kernels are built with CONFIG_NR_CPUS=32.
_KERNEL_MAX = 31
# The upstream default; vendors' own (Pixel's ``sched_pixel``) are not in the chip files.
_GOVERNOR = "schedutil"


class CpuPlacement(NamedTuple):
    """The host cores a container runs on, how many, and the directory of what it sees."""

    cpuset: str
    cores: int
    directory: Path


def _span(first: int, last: int) -> str:
    """A kernel cpu list of one range: ``0-7``, or ``0`` alone."""
    return str(first) if first == last else f"{first}-{last}"


def cpuset(cores: int, index: int, host_cores: int) -> str:
    """The host cores of pod ``index``: its own slice while the host has room, else the first."""
    first = index * cores if host_cores >= (index + 1) * cores else 0
    return _span(first, first + cores - 1)


class _Placed(NamedTuple):
    """A cluster cut to the core count: its number, itself, its cpus and their kinds."""

    number: int
    cluster: CpuCluster
    cpus: list[int]
    kinds: list[CpuCores]


def _clusters(soc: Soc, cores: int) -> list[_Placed]:
    """Each cluster with a cpu among the chip's first ``cores``, cut to them."""
    placed: list[_Placed] = []
    first = 0
    for number, cluster in enumerate(soc.clusters):
        kinds = [kind for kind in cluster.cores for _ in range(kind.count)]
        cpus = list(range(first, min(first + cluster.count, cores)))
        if cpus:
            placed.append(_Placed(number, cluster, cpus, kinds[: len(cpus)]))
        first += cluster.count
    return placed


def cpuinfo_text(soc: Soc, cores: int) -> str:
    """``/proc/cpuinfo`` of the chip's first ``cores`` cores, as an arm64 kernel prints it."""
    blocks = [
        f"processor\t: {cpu}\n"
        f"BogoMIPS\t: {soc.bogomips}\n"
        f"Features\t: {soc.features}\n"
        f"CPU implementer\t: {kind.implementer}\n"
        "CPU architecture: 8\n"
        f"CPU variant\t: {kind.variant}\n"
        f"CPU part\t: {kind.part}\n"
        f"CPU revision\t: {kind.revision}\n\n"
        for placed in _clusters(soc, cores)
        for cpu, kind in zip(placed.cpus, placed.kinds, strict=True)
    ]
    return "".join(blocks)


def cpu_tree(soc: Soc, cores: int) -> dict[str, str]:
    """``/sys/devices/system/cpu`` of the chip's first ``cores`` cores: path -> content."""
    every = _span(0, cores - 1)
    tree = {
        "possible": every,
        "present": every,
        "online": every,
        "offline": "",
        "kernel_max": str(_KERNEL_MAX),
    }
    for number, cluster, cpus, _kinds in _clusters(soc, cores):
        frequencies = cluster.frequencies_khz
        related = " ".join(map(str, cpus))
        for cpu in cpus:
            topology = f"cpu{cpu}/topology/"
            freq = f"cpu{cpu}/cpufreq/"
            tree |= {
                f"cpu{cpu}/online": "1",
                f"{topology}physical_package_id": str(number),
                f"{topology}core_id": str(cpu - cpus[0]),
                f"{topology}cluster_cpus_list": _span(cpus[0], cpus[-1]),
                f"{topology}core_siblings_list": str(cpu),
                f"{topology}thread_siblings_list": str(cpu),
                f"{freq}cpuinfo_min_freq": str(frequencies[0]),
                f"{freq}cpuinfo_max_freq": str(frequencies[-1]),
                f"{freq}scaling_min_freq": str(frequencies[0]),
                f"{freq}scaling_max_freq": str(frequencies[-1]),
                f"{freq}scaling_cur_freq": str(frequencies[0]),
                f"{freq}scaling_available_frequencies": " ".join(map(str, frequencies)) + " ",
                f"{freq}related_cpus": related,
                f"{freq}affected_cpus": related,
                f"{freq}scaling_governor": _GOVERNOR,
            }
    return {path: content + "\n" for path, content in tree.items()}


def _write(path: Path, content: str) -> None:
    """Write ``content`` unless the file has it already: a mounted file is left alone."""
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def write_topology(key: str, soc: Soc, cores: int) -> Path:
    """The host directory of chip ``key`` at ``cores`` cores, its files written: idempotent.

    It holds ``cpuinfo``, ``cpuinfo.rc`` (the init script) and ``cpu/`` (the sysfs tree).
    """
    directory = settings.emulator.cpu_topology_dir.resolve() / f"{key}-{cores}"
    _write(directory / "cpuinfo", cpuinfo_text(soc, cores))
    _write(directory / "cpuinfo.rc", _RC_TEXT)
    for path, content in cpu_tree(soc, cores).items():
        _write(directory / "cpu" / path, content)
    return directory


async def host_cores() -> int:
    """The CPUs Docker's VM has (not Windows's count): what a cpuset can name."""
    return int((await docker("info", "--format", "{{.NCPU}}")).strip())


async def place_cpus(key: str, index: int) -> CpuPlacement:
    """Pod ``index``'s host cores and the topology of chip ``key`` it sees, written."""
    soc = load_socs()[key]
    host = await host_cores()
    cores = min(soc.cores, host)
    directory = await asyncio.to_thread(write_topology, key, soc, cores)
    return CpuPlacement(cpuset(cores, index, host), cores, directory)
