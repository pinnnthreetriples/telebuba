"""A parked pod: Android paused alone, thawed before it is stopped or restarted."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.emulator import pod
from tests.core.emulator.test_pod import _PROXY, _SPEC, _docker, _verbs

if TYPE_CHECKING:
    from tests.core.emulator.conftest import Console

_PAUSED = "0|telebuba-acc-0|paused\n"


@pytest.mark.asyncio
async def test_a_pod_is_parked_by_pausing_android_alone(console: Console) -> None:
    console.answer = _docker()

    assert await pod.pause_pod("telebuba-acc-0") is True
    assert await pod.pause_pod("telebuba-acc-2") is False, "a stopped pod has nothing to pause"

    assert _verbs(console) == ["pause telebuba-acc-0"], "the gateway keeps its tunnel up"


@pytest.mark.asyncio
async def test_a_parked_pod_is_thawed_then_killed_when_stopped(console: Console) -> None:
    console.answer = _docker(_PAUSED)

    await pod.stop_pod("telebuba-acc-0")

    assert _verbs(console) == [
        "unpause telebuba-acc-0",
        "kill telebuba-acc-0",
        "stop telebuba-acc-0-net",
    ], "no grace: the app must not get to reconnect"


@pytest.mark.asyncio
async def test_only_a_pod_still_parked_is_stopped_by_its_timer(console: Console) -> None:
    console.answer = _docker(_PAUSED)
    assert await pod.stop_paused_pod("telebuba-acc-0") is True

    console.calls.clear()
    console.answer = _docker()
    assert await pod.stop_paused_pod("telebuba-acc-0") is False, "someone opened it since"
    assert _verbs(console) == []


@pytest.mark.asyncio
async def test_parked_pods_are_stopped_with_the_running_ones(console: Console) -> None:
    console.answer = _docker(_PAUSED + "1|telebuba-acc-1|running\n")

    assert await pod.stop_running_pods() == 3

    verbs = _verbs(console)
    assert "kill telebuba-acc-0" in verbs
    assert "stop telebuba-acc-1" in verbs


@pytest.mark.asyncio
async def test_a_parked_pod_is_halted_before_a_restart(console: Console) -> None:
    console.answer = _docker(_PAUSED)

    await pod.start_pod("telebuba-acc-0", _SPEC, _PROXY)

    verbs = _verbs(console)
    assert verbs.index("kill telebuba-acc-0") < verbs.index("start telebuba-acc-0-net")
