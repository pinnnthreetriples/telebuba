"""The second-device crash matrix: every step, every way to fail, then a restart and the repair."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import fetch_account
from tests.custody import _second_world as sd
from tests.custody._flows import assert_a_live_key_is_on_disk, matrix, restart_and_repair
from tests.custody._world import Fault, World

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path, sd.ROLES)


async def _crash(world: World, monkeypatch: pytest.MonkeyPatch, faults: tuple[Fault, ...]) -> None:
    await sd.start(world, monkeypatch)
    world.faults.extend(faults)
    await world.run(sd.second_flow)
    assert faults[0] in world.fired, f"the matrix is stale, {faults[0].label} never ran"
    assert_a_live_key_is_on_disk(world, "after the crash")


@pytest.mark.asyncio
async def test_a_clean_sign_in_walks_exactly_the_steps_the_matrix_lists(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    await sd.start(world, monkeypatch)

    await world.run(sd.second_flow)

    assert world.outcome is None
    assert world.trace == [name for name, _ in sd.STEPS]
    account = await fetch_account("acc")
    assert account is not None
    assert account.second_session_name == sd.NAME


# The repair meets a Telegram that is unreachable once: its logout is lost before or after it
# took effect. The operator's retry then settles it.
_REPAIR_TROUBLE = [
    pytest.param((), id="repair_healthy"),
    pytest.param((Fault("leftover.logout", "raised"),), id="repair_logout_no_effect"),
    pytest.param(
        (Fault("leftover.logout", "raised", landed=True),), id="repair_logout_answer_lost"
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("repair_trouble", _REPAIR_TROUBLE)
@pytest.mark.parametrize("fault", matrix(sd.STEPS, shielded=sd.SHIELDED))
async def test_second_device(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
    fault: Fault,
    repair_trouble: tuple[Fault, ...],
) -> None:
    await _crash(world, monkeypatch, (fault, *repair_trouble))

    await restart_and_repair(world)

    await sd.check_after_repair(world)
    await sd.retry_after_repair(world)


# A failure that makes the flow undo itself, and then the undo meets trouble of its own.
_FAILURES = [
    pytest.param(Fault("second.sign_in", "raised", landed=True), id="sign_in_answer_lost"),
    pytest.param(Fault("second.sign_in", "cancelled", landed=True), id="sign_in_cancelled"),
    pytest.param(Fault("second.get_me", "raised"), id="get_me_fails"),
    pytest.param(Fault("db.record_device", "raised"), id="record_fails"),
]
_UNDO_TROUBLE = [
    pytest.param(Fault("second.log_out", "raised"), id="logout_no_effect"),
    pytest.param(Fault("second.log_out", "raised", landed=True), id="logout_answer_lost"),
    pytest.param(Fault("second.log_out", "cancelled"), id="logout_cancelled"),
    pytest.param(Fault("file.quarantine", "raised"), id="file_removal_fails"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("trouble", _UNDO_TROUBLE)
@pytest.mark.parametrize("failure", _FAILURES)
async def test_second_device_whose_undo_fails(
    world: World, monkeypatch: pytest.MonkeyPatch, failure: Fault, trouble: Fault
) -> None:
    await _crash(world, monkeypatch, (failure, trouble))
    assert trouble in world.fired, "the undo never tried that step"

    await restart_and_repair(world)

    await sd.check_after_repair(world)
    await sd.retry_after_repair(world)


@pytest.mark.asyncio
async def test_second_device_whose_journal_was_never_opened_is_still_not_leaked(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The journal cannot be written (a locked database), then the sign-in lands with its
    # answer lost and the logout that would undo it is lost too: no intent, a live
    # authorization, a file that is its only handle.
    faults = (
        Fault("journal.open", "raised"),
        Fault("second.sign_in", "raised", landed=True),
        Fault("second.log_out", "raised"),
    )
    await _crash(world, monkeypatch, faults)

    await restart_and_repair(world)

    await sd.check_after_repair(world)
    await sd.retry_after_repair(world)
