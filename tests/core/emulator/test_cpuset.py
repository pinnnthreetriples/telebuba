"""CPU pinning: each pod's Android on its own slice of the Docker host's cores, opt-in."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.emulator import _profile, _topology, host, pod
from core.emulator._cpuset import cpuset_for
from core.emulator._errors import EmulatorError
from tests.core.emulator.test_pod import _PROXY, _SPEC, _call, _docker
from tests.core.emulator.test_topology import CHIP

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.core.emulator.conftest import Console

_NCPU = ["info", "--format", "{{.NCPU}}"]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host, "_cpus_seen", 0)


def _with_cpus(answer: str, docker: Callable[[list[str]], str]) -> Callable[[list[str]], str]:
    """``docker`` that answers ``docker info`` with ``answer`` as the host's core count."""

    def reply(argv: list[str]) -> str:
        return f"{answer}\n" if argv[1:] == _NCPU else docker(argv)

    return reply


def _asked(console: Console) -> int:
    return sum(call[1:] == _NCPU for call in console.calls)


@pytest.mark.parametrize(
    ("index", "size", "host_cpus", "expected"),
    [
        (0, 8, 16, "0-7"),
        (1, 8, 16, "8-15"),
        (2, 8, 16, "0-7"),  # past the last slice: the first again
        (3, 8, 16, "8-15"),
        (2, 8, 20, "0-7"),  # cores 16-19 are no whole slice
        (1001, 4, 32, "4-7"),
        (0, 1, 1, "0"),
        (5, 1, 4, "1"),
        (3, 8, 6, "0-5"),  # fewer cores than one slice: all of them
        (3, 8, 8, "0-7"),
        (7, 4, 1, "0"),
    ],
)
def test_a_pod_takes_the_slice_its_index_picks(
    index: int, size: int, host_cpus: int, expected: str
) -> None:
    assert cpuset_for(index, size, host_cpus) == expected


def test_every_slice_lies_within_the_host() -> None:
    for host_cpus in range(1, 40):
        for size in range(1, 12):
            for index in range(50):
                first, _, last = cpuset_for(index, size, host_cpus).partition("-")
                assert 0 <= int(first) <= int(last or first) < host_cpus


@pytest.mark.asyncio
async def test_the_docker_hosts_cores_are_asked_once(console: Console) -> None:
    console.answer = lambda argv: "16\n" if argv[1:] == _NCPU else ""

    assert await host.host_cpus() == 16
    assert await host.host_cpus() == 16

    assert console.calls == [[str(settings.emulator.docker_path), *_NCPU]]


@pytest.mark.parametrize("answer", ["", "sixteen", "0", "-4", "16.5"])
@pytest.mark.asyncio
async def test_an_answer_that_is_no_core_count_is_a_failed_command(
    console: Console, answer: str
) -> None:
    console.answer = lambda _argv: answer

    with pytest.raises(EmulatorError) as refusal:
        await host.host_cpus()

    assert refusal.value.code == "emulator_command_failed"
    assert host._cpus_seen == 0, "asked again next time"


@pytest.mark.asyncio
async def test_a_docker_that_cannot_answer_is_a_failed_command(console: Console) -> None:
    def answer(_argv: list[str]) -> str:
        code = "emulator_command_failed"
        raise EmulatorError(code)

    console.answer = answer

    with pytest.raises(EmulatorError) as refusal:
        await host.host_cpus()

    assert refusal.value.code == "emulator_command_failed"


@pytest.mark.asyncio
async def test_pinning_is_off_by_default(console: Console) -> None:
    console.answer = _with_cpus("16", _docker())

    await pod.start_pod("telebuba-new", _SPEC, _PROXY)

    assert _asked(console) == 0, "the host is not asked"
    android = _call(console, "create", "telebuba-new")
    assert "--cpuset-cpus" not in android
    assert android[android.index("--cpus") + 1] == str(settings.emulator.cpu_cores)


@pytest.mark.asyncio
async def test_a_new_android_is_pinned_to_its_pods_slice(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "cpuset_cores", 8)
    console.answer = _with_cpus("16", _docker())

    await pod.start_pod("telebuba-new", _SPEC, _PROXY)

    android = _call(console, "create", "telebuba-new")
    assert "telebuba.index=1" in android
    assert android[android.index("--cpuset-cpus") + 1] == "8-15"
    assert "--cpus" in android, "its time share is still capped"


@pytest.mark.asyncio
async def test_an_android_found_again_is_not_pinned_anew(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "cpuset_cores", 8)
    console.answer = _with_cpus("16", _docker())

    await pod.start_pod("telebuba-acc-0", _SPEC, _PROXY)

    assert _asked(console) == 0
    assert [call for call in console.calls if call[1] == "create"] == []


@pytest.mark.asyncio
async def test_a_host_that_cannot_say_its_cores_leaves_the_android_whose_gateway_went(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "cpuset_cores", 8)
    console.answer = _with_cpus("unknown", _docker(gateways=""))

    with pytest.raises(EmulatorError) as refused:
        await pod.start_pod("telebuba-acc-0", _SPEC, _PROXY)

    assert refused.value.code == "emulator_command_failed"
    assert [call for call in console.calls if call[1] in {"rm", "create"}] == []


@pytest.mark.asyncio
async def test_a_phone_with_a_chip_is_pinned_by_its_chip_alone(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    for module in (_topology, _profile):
        monkeypatch.setattr(module, "load_socs", lambda: {"chip": CHIP})
    monkeypatch.setattr(settings.emulator, "cpuset_cores", 2)
    console.answer = _with_cpus("16", _docker())

    await pod.start_pod("telebuba-new", _SPEC._replace(soc="chip"), _PROXY)

    android = _call(console, "create", "telebuba-new")
    assert android.count("--cpuset-cpus") == 1
    assert android[android.index("--cpuset-cpus") + 1] == "4-7", "the chip's 4 cores, slice 1"
    assert host._cpus_seen == 0, "the chipless probe is not asked"
