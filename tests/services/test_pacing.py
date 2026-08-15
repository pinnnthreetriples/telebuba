"""The shared send pacer and the shared human-pause draw."""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

import pytest

from services import pacing

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _reset_pacer() -> Iterator[None]:
    pacing.reset_for_tests()
    yield
    pacing.reset_for_tests()


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record every sleep instead of serving it — the gate, not the wall clock.

    ``time.monotonic`` is advanced by whatever was slept so the second caller
    observes the elapsed gap it would really have seen.
    """
    clock = {"now": 1000.0}
    calls: list[float] = []

    async def _sleep(seconds: float) -> None:
        calls.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(pacing.asyncio, "sleep", _sleep)
    monkeypatch.setattr(pacing.time, "monotonic", lambda: clock["now"])
    return calls


@pytest.mark.asyncio
async def test_the_first_slot_for_a_key_never_waits(slept: list[float]) -> None:
    await pacing.await_send_slot("acc-1", 30.0)

    assert slept == []


@pytest.mark.asyncio
async def test_a_second_slot_waits_out_the_remaining_gap(slept: list[float]) -> None:
    await pacing.await_send_slot("acc-1", 30.0)
    await pacing.await_send_slot("acc-1", 30.0)

    assert slept == [30.0]


@pytest.mark.asyncio
async def test_a_non_positive_gap_disables_the_gate_without_touching_the_clock(
    slept: list[float],
) -> None:
    """An opted-out caller must not perturb the spacing of one that opted in."""
    await pacing.await_send_slot("acc-1", 0.0)
    await pacing.await_send_slot("acc-1", -5.0)
    await pacing.await_send_slot("acc-1", 30.0)

    assert slept == []


@pytest.mark.asyncio
async def test_keys_are_paced_independently(slept: list[float]) -> None:
    """``join:acc-1`` and ``acc-1`` are two tempos for one account, by design."""
    await pacing.await_send_slot("acc-1", 30.0)
    await pacing.await_send_slot("join:acc-1", 30.0)
    await pacing.await_send_slot("acc-2", 30.0)

    assert slept == []


@pytest.mark.asyncio
async def test_concurrent_callers_on_one_key_are_spaced_not_merged(
    slept: list[float],
) -> None:
    """The lock is what makes a burst queue up instead of all firing at once."""
    await asyncio.gather(*(pacing.await_send_slot("acc-1", 10.0) for _ in range(3)))

    assert slept == [10.0, 10.0]


@pytest.mark.asyncio
async def test_the_jitter_is_the_callers_so_each_call_may_ask_for_a_different_gap(
    slept: list[float],
) -> None:
    await pacing.await_send_slot("acc-1", 30.0)
    await pacing.await_send_slot("acc-1", 45.0)

    assert slept == [45.0]


def test_human_delay_collapses_an_empty_range() -> None:
    assert pacing.human_delay(7.0, 7.0, rng=random.SystemRandom(), mu=-0.8, sigma=0.6) == 7.0


def test_human_delay_treats_a_reversed_range_as_the_range_it_describes() -> None:
    """Swapped bounds are a caller mistake worth surviving, not worth collapsing."""

    class _Half(random.Random):
        def lognormvariate(self, mu: float, sigma: float) -> float:  # noqa: ARG002
            return 0.5

    assert pacing.human_delay(30.0, 10.0, rng=_Half(), mu=-0.8, sigma=0.6) == 20.0


def test_human_delay_stays_inside_the_range_even_on_a_runaway_draw() -> None:
    class _Extreme(random.Random):
        def lognormvariate(self, mu: float, sigma: float) -> float:  # noqa: ARG002
            return 10.0**6

    assert pacing.human_delay(10.0, 30.0, rng=_Extreme(), mu=-0.8, sigma=0.6) == 30.0


def test_human_delay_maps_the_drawn_fraction_onto_the_range() -> None:
    class _Half(random.Random):
        def lognormvariate(self, mu: float, sigma: float) -> float:  # noqa: ARG002
            return 0.25

    assert pacing.human_delay(10.0, 30.0, rng=_Half(), mu=-0.8, sigma=0.6) == 15.0


def test_human_delay_passes_the_shape_through_to_the_injected_rng() -> None:
    """Each domain keeps its own log-normal shape; the function has no opinion."""
    seen: list[tuple[float, float]] = []

    class _Recording(random.Random):
        def lognormvariate(self, mu: float, sigma: float) -> float:
            seen.append((mu, sigma))
            return 0.5

    pacing.human_delay(0.0, 1.0, rng=_Recording(), mu=-1.5, sigma=0.9)

    assert seen == [(-1.5, 0.9)]


@pytest.mark.parametrize("draw", [0.0, 0.1, 0.5, 0.99, 1.0])
def test_human_delay_never_leaves_the_bounds(draw: float) -> None:
    class _Fixed(random.Random):
        def lognormvariate(self, mu: float, sigma: float) -> float:  # noqa: ARG002
            return draw

    assert 10.0 <= pacing.human_delay(10.0, 30.0, rng=_Fixed(), mu=-0.8, sigma=0.6) <= 30.0
