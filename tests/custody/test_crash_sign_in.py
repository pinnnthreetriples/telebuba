"""The new-API sign-in crash matrix: the key a sign-in makes carries the API that made it."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.custody import _sign_in_world as si
from tests.custody._flows import assert_a_live_key_is_on_disk, matrix, restart_and_repair
from tests.custody._world import OLD, Fault, World

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path, si.ROLES)


@pytest.mark.asyncio
async def test_a_clean_sign_in_walks_exactly_the_steps_the_matrix_lists(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    await si.start(world, monkeypatch)

    await world.run(si.sign_in_flow)

    assert world.outcome is None
    assert world.trace == [name for name, _ in si.STEPS]
    assert world.server.live == {OLD}
    assert await si.stored_api() == si.NEW_API_ID


# The matrix found a sign-in whose API was never recorded closed as done; fixed since.
_KNOWN: dict[str, str] = {}


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", matrix(si.STEPS, known=_KNOWN))
async def test_sign_in(world: World, monkeypatch: pytest.MonkeyPatch, fault: Fault) -> None:
    await si.start(world, monkeypatch)
    world.faults.append(fault)
    await world.run(si.sign_in_flow)
    assert fault in world.fired, f"the matrix is stale, {fault.label} never ran"
    assert_a_live_key_is_on_disk(world, "after the crash", none_live_ok=True)

    await restart_and_repair(world)

    await si.check_after_repair(world)
    await si.retry_after_repair(world)
