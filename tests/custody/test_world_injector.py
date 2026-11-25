"""The failure injector itself: a cancellation at a worker-thread step lands at that step.

asyncio completes a thread call inside the awaiting step when the thread finished before the
loop attached to its future: the await then never yields, and a cancellation that was only
queued would reach the flow one await later, after it ran on as if the step had succeeded.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import TYPE_CHECKING, Any

import pytest

from tests.custody._world import Fault, World

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


class _FinishedFirst(concurrent.futures.ThreadPoolExecutor):
    """An executor whose call has finished, if it can, before the loop sees its future."""

    def submit(
        self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any
    ) -> concurrent.futures.Future[Any]:
        future = super().submit(fn, *args, **kwargs)
        concurrent.futures.wait([future], timeout=0.2)
        return future


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [Fault("file.copy", "cancelled"), Fault("file.copy", "cancelled", landed=True)],
    ids=["no_effect", "answer_lost"],
)
async def test_a_cancellation_at_a_thread_step_never_lets_the_flow_run_on(
    tmp_path: Path, fault: Fault
) -> None:
    world = World(tmp_path, {})
    world.faults.append(fault)
    seen: list[str] = []

    async def flow() -> None:
        loop = asyncio.get_running_loop()
        with _FinishedFirst(max_workers=1) as pool:
            await loop.run_in_executor(
                pool, world.sync_step, "file.copy", lambda: seen.append("copied")
            )
            seen.append("ran on")

    await world.run(flow)

    assert isinstance(world.outcome, asyncio.CancelledError)
    assert seen == (["copied"] if fault.landed else [])
