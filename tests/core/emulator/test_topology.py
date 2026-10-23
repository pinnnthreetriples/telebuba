"""The chip a container shows: its cores, the host cores it runs on, its cpuinfo and cpufreq."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.emulator import _topology
from core.emulator._blueprints import CpuCluster, CpuCores, Soc, load_blueprints, load_socs

if TYPE_CHECKING:
    from pathlib import Path

    from tests.core.emulator.conftest import Console

_LITTLE = CpuCluster(
    cores=(CpuCores(count=2, implementer="0x41", variant="0x2", part="0xd46", revision=1),),
    frequencies_khz=(307200, 1094400),
)
# One policy of two kinds of core, as SM8550's cpu3-6 (A715 + A710).
_BIG = CpuCluster(
    cores=(
        CpuCores(count=1, implementer="0x41", variant="0x1", part="0xd4d", revision=0),
        CpuCores(count=1, implementer="0x41", variant="0x1", part="0xd4e", revision=0),
    ),
    frequencies_khz=(499200, 2016000, 3187200),
)
CHIP = Soc(
    model="SM0000",
    manufacturer="QTI",
    bogomips="38.40",
    features="fp asimd",
    clusters=(_LITTLE, _BIG),
    sources=("https://example.org/dump",),
)
# Real phones' core counts: Exynos 2400 has 10, Tensor G3 9.
_CORES = {
    "gs201": 8,
    "zuma": 9,
    "sm8550": 8,
    "sm8550_ac": 8,
    "sm7475": 8,
    "mt6877": 8,
    "s5e8835": 8,
    "s5e8845": 8,
    "s5e9945": 10,
}


def test_every_blueprints_chip_exists_and_its_clusters_add_up() -> None:
    socs = load_socs()
    named = {blueprint.soc for blueprint in load_blueprints()}
    assert named - {None} == socs.keys(), "every chip file is some phone's"
    assert {key: soc.cores for key, soc in socs.items()} == _CORES
    for key, soc in socs.items():
        maxima = [cluster.frequencies_khz[-1] for cluster in soc.clusters]
        assert maxima == sorted(maxima), f"{key}: little clusters first"


def test_pixel_8_matches_its_own_dump() -> None:
    """Tensor G3 against a Pixel 8's (pytorch/cpuinfo test/mock/pixel-8.h)."""
    zuma = load_socs()["zuma"]
    tree = _topology.cpu_tree(zuma, 9)
    cpuinfo = _topology.cpuinfo_text(zuma, 9)

    assert cpuinfo.count("processor") == 9
    assert cpuinfo.endswith(
        "processor\t: 8\nBogoMIPS\t: 49.15\n"
        "Features\t: fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid "
        "asimdrdm jscvt fcma lrcpc dcpop sha3 sm3 sm4 asimddp sha512 sve asimdfhm dit uscat "
        "ilrcpc flagm ssbs sb paca pacg dcpodp sve2 sveaes svepmull svebitperm svesha3 svesm4 "
        "flagm2 frint svei8mm svebf16 i8mm bti\nCPU implementer\t: 0x41\n"
        "CPU architecture: 8\nCPU variant\t: 0x1\nCPU part\t: 0xd4e\nCPU revision\t: 0\n\n"
    )
    assert tree["possible"] == "0-8\n"
    assert tree["kernel_max"] == "31\n"
    assert tree["cpu4/cpufreq/scaling_available_frequencies"] == (
        "402000 578000 697000 712000 910000 1065000 1221000 1328000 1418000 1572000 "
        "1836000 1945000 2130000 2245000 2367000 \n"
    )
    assert tree["cpu4/cpufreq/related_cpus"] == "4 5 6 7\n"
    assert tree["cpu0/cpufreq/cpuinfo_min_freq"] == "324000\n"
    assert tree["cpu8/cpufreq/cpuinfo_max_freq"] == "2914000\n"
    assert tree["cpu5/topology/physical_package_id"] == "1\n"
    assert tree["cpu5/topology/core_id"] == "1\n"
    assert tree["cpu5/topology/cluster_cpus_list"] == "4-7\n"
    assert tree["cpu5/topology/core_siblings_list"] == "5\n"


def test_a_cluster_lists_its_frequencies_ascending() -> None:
    with pytest.raises(ValueError, match="ascending"):
        CpuCluster.model_validate(_LITTLE.model_dump() | {"frequencies_khz": (1094400, 307200)})


@pytest.mark.parametrize(
    ("cores", "index", "host", "expected"),
    [
        (8, 0, 16, "0-7"),
        (8, 1, 16, "8-15"),
        (8, 2, 16, "0-7"),  # no third slice: round-robin from the first
        (8, 3, 16, "8-15"),
        (8, 9, 64, "8-15"),  # pods past the eighth spread, not all on 0-7
        (8, 29, 64, "40-47"),
        (9, 1, 16, "0-8"),
        (4, 3, 16, "12-15"),
        (1, 0, 1, "0"),
    ],
)
def test_a_pod_has_its_own_slice_while_the_host_has_room(
    cores: int, index: int, host: int, expected: str
) -> None:
    assert _topology.cpuset(cores, index, host) == expected


def test_cpuinfo_is_what_an_arm64_kernel_prints_per_core() -> None:
    little = (
        "BogoMIPS\t: 38.40\nFeatures\t: fp asimd\nCPU implementer\t: 0x41\n"
        "CPU architecture: 8\nCPU variant\t: 0x2\nCPU part\t: 0xd46\nCPU revision\t: 1\n\n"
    )
    mid = (
        "BogoMIPS\t: 38.40\nFeatures\t: fp asimd\nCPU implementer\t: 0x41\n"
        "CPU architecture: 8\nCPU variant\t: 0x1\nCPU part\t: 0xd4d\nCPU revision\t: 0\n\n"
    )
    big = mid.replace("0xd4d", "0xd4e")
    assert _topology.cpuinfo_text(CHIP, 4) == (
        f"processor\t: 0\n{little}processor\t: 1\n{little}"
        f"processor\t: 2\n{mid}processor\t: 3\n{big}"
    ), "one policy, two kinds of core"


def test_cpufreq_and_topology_follow_each_cluster() -> None:
    tree = _topology.cpu_tree(CHIP, 4)

    assert tree["possible"] == tree["present"] == tree["online"] == "0-3\n"
    assert tree["offline"] == "\n"
    assert tree["kernel_max"] == "31\n"
    assert tree["cpu3/cpufreq/scaling_available_frequencies"] == "499200 2016000 3187200 \n"
    assert tree["cpu3/cpufreq/cpuinfo_min_freq"] == "499200\n"
    assert tree["cpu3/cpufreq/cpuinfo_max_freq"] == tree["cpu3/cpufreq/scaling_max_freq"]
    assert tree["cpu3/cpufreq/scaling_max_freq"] == "3187200\n"
    assert tree["cpu3/cpufreq/scaling_cur_freq"] == "499200\n", "static"
    assert tree["cpu2/cpufreq/related_cpus"] == tree["cpu3/cpufreq/affected_cpus"] == "2 3\n"
    assert tree["cpu0/cpufreq/cpuinfo_max_freq"] == "1094400\n"
    assert tree["cpu1/cpufreq/scaling_governor"] == "schedutil\n"
    assert tree["cpu3/topology/physical_package_id"] == "1\n", "one per cluster"
    assert tree["cpu3/topology/core_id"] == "1\n"
    assert tree["cpu3/topology/cluster_cpus_list"] == "2-3\n"
    assert tree["cpu3/topology/thread_siblings_list"] == "3\n"
    assert tree["cpu0/topology/core_siblings_list"] == "0\n"


def test_a_smaller_host_shows_as_many_cores_as_it_runs() -> None:
    tree = _topology.cpu_tree(CHIP, 3)

    assert tree["online"] == "0-2\n"
    assert "cpu3/online" not in tree
    assert tree["cpu2/cpufreq/related_cpus"] == "2\n"
    assert _topology.cpuinfo_text(CHIP, 3).count("processor") == 3


def test_the_topology_is_written_once_per_chip_and_core_count(tmp_path: Path) -> None:
    directory = _topology.write_topology("chip", CHIP, 4)

    assert directory == settings.emulator.cpu_topology_dir.resolve() / "chip-4"
    assert directory.is_relative_to(tmp_path)
    cpuinfo = directory / "cpuinfo"
    assert cpuinfo.read_text(encoding="utf-8") == _topology.cpuinfo_text(CHIP, 4)
    assert (directory / "cpu/cpu2/cpufreq/cpuinfo_max_freq").read_text() == "3187200\n"
    rc = (directory / "cpuinfo.rc").read_text(encoding="utf-8")
    assert "mount none /dev/cpuinfo /proc/cpuinfo bind\n" in rc
    stamp = cpuinfo.stat().st_mtime_ns

    assert _topology.write_topology("chip", CHIP, 4) == directory
    assert cpuinfo.stat().st_mtime_ns == stamp, "an unchanged file is not written again"


@pytest.mark.asyncio
async def test_the_host_cores_come_from_docker_not_windows(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_topology, "load_socs", lambda: {"chip": CHIP})
    console.answer = lambda argv: "6\n" if argv[1] == "info" else ""

    placement = await _topology.place_cpus("chip", 1)

    assert console.calls[0][1:] == ["info", "--format", "{{.NCPU}}"]
    assert placement.cpuset == "0-3", "6 host cores hold one slice of 4"
    assert placement.directory.name == "chip-4"


@pytest.mark.asyncio
async def test_the_host_cores_are_read_once_per_process(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_topology, "load_socs", lambda: {"chip": CHIP})
    console.answer = lambda argv: "6\n" if argv[1] == "info" else ""

    await _topology.place_cpus("chip", 1)
    await _topology.place_cpus("chip", 2)

    assert [call[1] for call in console.calls] == ["info"], "no docker info at the second start"
