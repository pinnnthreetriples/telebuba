"""Starts of different pods run side by side; starts of one pod queue behind each other."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.emulator import pod
from tests.core.emulator.test_pod import _PROXY, _SPEC, _docker

if TYPE_CHECKING:
    from tests.core.emulator.conftest import Console

_STOPPED = "0|telebuba-a|exited\n1|telebuba-b|exited\n"
_STOPPED_GATEWAYS = "0|telebuba-a-net|exited\n1|telebuba-b-net|exited\n"


class _SlowCheck:
    """``_stale_gateway`` that holds ``slow``'s check until ``gate`` is set; records each."""

    def __init__(self, slow: str) -> None:
        self.slow = slow
        self.gate = asyncio.Event()
        self.waiting = asyncio.Event()
        self.checked: list[str] = []

    async def __call__(self, gateway: str, _configured: str | None) -> bool:
        self.checked.append(gateway)
        if gateway == self.slow:
            self.waiting.set()
            await self.gate.wait()
        return False


@pytest.mark.asyncio
async def test_a_pod_held_up_by_its_image_check_does_not_hold_up_another_pod(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    console.answer = _docker(_STOPPED, _STOPPED_GATEWAYS)
    check = _SlowCheck("telebuba-a-net")
    monkeypatch.setattr(pod, "_stale_gateway", check)
    first = asyncio.create_task(pod.start_pod("telebuba-a", _SPEC, _PROXY))
    await check.waiting.wait()

    other = await asyncio.wait_for(pod.start_pod("telebuba-b", _SPEC, _PROXY), timeout=5)

    assert other.instance == (1, "telebuba-b", True, False)
    assert not first.done(), "the first start is still at its check"
    check.gate.set()
    assert (await first).instance == (0, "telebuba-a", True, False)


@pytest.mark.asyncio
async def test_a_new_pod_held_up_by_its_gpu_probe_does_not_hold_up_another_new_pod(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    console.answer = _docker("", "")
    gate, probing = asyncio.Event(), asyncio.Event()

    async def gpu_mode() -> str:
        if not probing.is_set():
            probing.set()
            await gate.wait()
        return "guest"

    monkeypatch.setattr(pod, "gpu_mode", gpu_mode)
    first = asyncio.create_task(pod.start_pod("telebuba-a", _SPEC, _PROXY))
    await probing.wait()

    other = await asyncio.wait_for(pod.start_pod("telebuba-b", _SPEC, _PROXY), timeout=5)
    gate.set()
    late = await first

    assert other.instance.index == 0, "the pod not held up took the first free index"
    assert late.instance.index == 1, "the held-up one picked its index after, never the same one"


@pytest.mark.asyncio
async def test_two_starts_of_one_pod_queue_behind_each_other(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    console.answer = _docker(_STOPPED, _STOPPED_GATEWAYS)
    check = _SlowCheck("telebuba-a-net")
    monkeypatch.setattr(pod, "_stale_gateway", check)
    first = asyncio.create_task(pod.start_pod("telebuba-a", _SPEC, _PROXY))
    await check.waiting.wait()
    second = asyncio.create_task(pod.start_pod("telebuba-a", _SPEC, _PROXY))
    await asyncio.sleep(0.05)

    assert check.checked == ["telebuba-a-net"], "the second start waits before it looks"
    check.gate.set()
    await asyncio.gather(first, second)
    assert check.checked == ["telebuba-a-net"] * 2
