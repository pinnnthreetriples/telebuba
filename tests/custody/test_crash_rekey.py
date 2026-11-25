"""The re-key crash matrix: every step, every way to fail, then a restart and the repair."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories.account_rekeys import fetch_rekey
from tests.custody import _rekey_world as rk
from tests.custody._flows import assert_a_live_key_is_on_disk, matrix, restart_and_repair
from tests.custody._world import OLD, Fault, World

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path, rk.ROLES)


@pytest.mark.asyncio
async def test_a_clean_rekey_walks_exactly_the_steps_the_matrix_lists(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    await rk.start(world, monkeypatch)

    await world.run(rk.rekey_flow)

    assert world.outcome is None
    assert world.trace == [name for name, _ in rk.STEPS]
    assert world.server.live == {OLD, *world.server.created}
    assert await fetch_rekey("acc") is not None


# The repair meets a Telegram that is unreachable once: its logout is lost before or after it
# took effect. The operator's retry then settles it.
_REPAIR_TROUBLE = [
    pytest.param((), id="repair_healthy"),
    pytest.param((Fault("leftover.logout", "raised"),), id="repair_logout_no_effect"),
    pytest.param(
        (Fault("leftover.logout", "raised", landed=True),), id="repair_logout_answer_lost"
    ),
]


async def _crash(world: World, monkeypatch: pytest.MonkeyPatch, faults: tuple[Fault, ...]) -> None:
    await rk.start(world, monkeypatch)
    world.faults.extend(faults)
    await world.run(rk.rekey_flow)
    assert faults[0] in world.fired, f"the matrix is stale, {faults[0].label} never ran"
    assert_a_live_key_is_on_disk(world, "after the crash")


# Defects the matrix found and that are fixed since: none is expected to fail any more.
_KNOWN: dict[str, str] = {}


@pytest.mark.asyncio
@pytest.mark.parametrize("repair_trouble", _REPAIR_TROUBLE)
@pytest.mark.parametrize("fault", matrix(rk.STEPS, known=_KNOWN))
async def test_rekey(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
    fault: Fault,
    repair_trouble: tuple[Fault, ...],
) -> None:
    await _crash(world, monkeypatch, (fault, *repair_trouble))

    await restart_and_repair(world)

    await rk.check_after_repair(world)
    await rk.retry_after_repair(world)


# A failure that makes the flow undo itself, and then the undo meets trouble of its own: the
# logout is lost before or after it took effect, or the copy back of the old content fails.
_UNDO_STEPS = ("new.get_me", "new.list", "file.copy_aside", "file.copy_final")
_UNDO_TROUBLE = [
    pytest.param(Fault("new.log_out", "raised"), id="undo_logout_no_effect"),
    pytest.param(Fault("new.log_out", "raised", landed=True), id="undo_logout_answer_lost"),
    pytest.param(Fault("new.log_out", "cancelled"), id="undo_logout_cancelled"),
]
_INCOMPLETE = [
    pytest.param(
        (Fault("file.copy_final", kind, landed=True), Fault("file.copy_back", "raised")),
        id=f"copy_final_answer_lost_{kind}+copy_back_fails",
    )
    for kind in ("raised", "cancelled")
]


@pytest.mark.asyncio
@pytest.mark.parametrize("trouble", _UNDO_TROUBLE)
@pytest.mark.parametrize("step", _UNDO_STEPS)
async def test_rekey_whose_undo_fails(
    world: World, monkeypatch: pytest.MonkeyPatch, step: str, trouble: Fault
) -> None:
    faults = (Fault(step, "raised"), trouble)
    await _crash(world, monkeypatch, faults)
    assert trouble in world.fired, "the undo never tried to log out"

    await restart_and_repair(world)

    await rk.check_after_repair(world)
    await rk.retry_after_repair(world)


@pytest.mark.asyncio
@pytest.mark.parametrize("faults", _INCOMPLETE)
async def test_rekey_that_cannot_put_the_old_key_back_keeps_both_keys(
    world: World, monkeypatch: pytest.MonkeyPatch, faults: tuple[Fault, ...]
) -> None:
    await _crash(world, monkeypatch, faults)
    assert all(f in world.fired for f in faults)

    await restart_and_repair(world)

    # The account's file holds the new key; the only copy of the old key, which Telegram still
    # honours until the operator ends it, is kept (the side copy, or the quarantine).
    assert OLD in world.disk_keys().values()
    await rk.check_after_repair(world)
    await rk.retry_after_repair(world)
