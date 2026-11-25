"""The session-restore crash matrix, and its refusals: nothing is lost, nothing blocks a retry."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.session_vault import new_vault_key
from services.accounts._key_repair import repair_key_intents
from tests.custody import _restore_world as rw
from tests.custody._flows import matrix
from tests.custody._world import OLD, Fault, World, read_key
from tests.session_files import write_session_file

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path, {})


def _cases() -> list[object]:
    cases: list[object] = []
    for initial in ("missing", "keyless"):
        for param in matrix(rw.STEPS):
            fault = param.values[0]  # ty: ignore[unresolved-attribute]
            cases.append(pytest.param(initial, fault, id=f"{initial}:{fault.label}"))
    return cases


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", ["missing", "keyless"])
async def test_a_clean_restore_walks_exactly_the_steps_the_matrix_lists(
    world: World, monkeypatch: pytest.MonkeyPatch, initial: str
) -> None:
    await rw.start(world, monkeypatch, initial=initial)

    await world.run(rw.restore_flow)

    assert world.outcome is None
    assert world.trace == [name for name, _ in rw.STEPS]
    assert read_key(world.file("acc")) == OLD


@pytest.mark.asyncio
@pytest.mark.parametrize(("initial", "fault"), _cases())
async def test_restore(
    world: World, monkeypatch: pytest.MonkeyPatch, initial: str, fault: Fault
) -> None:
    await rw.start(world, monkeypatch, initial=initial)
    world.faults.append(fault)
    await world.run(rw.restore_flow)
    assert fault in world.fired, f"the matrix is stale, {fault.label} never ran"
    rw.assert_nothing_is_lost(world, initial=initial, when="after the crash")

    # A restart: nothing in the journal is about a restore, the repair has nothing to do.
    world.phase = "repair"
    assert await repair_key_intents() == 0
    assert await repair_key_intents() == 0
    rw.assert_nothing_is_lost(world, initial=initial, when="after repair")

    # The operator restores again (or the crash had already finished it): nothing may be in the way.
    world.phase = "flow"
    world.faults.clear()
    if read_key(world.file("acc")) is None:
        await rw.restore_flow()
    assert read_key(world.file("acc")) == OLD
    assert not list(world.sessions.glob("*.restoring")), "a staging file is left behind"


@pytest.mark.asyncio
async def test_a_copy_written_with_another_vault_key_is_refused_and_kept(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    await rw.start(world, monkeypatch, initial="keyless")
    right_key = settings.session_vault.key
    monkeypatch.setattr(settings.session_vault, "key", new_vault_key())
    await world.run(rw.restore_flow)

    assert "session_restore_copy_unreadable" in str(world.outcome)
    assert read_key(world.file("acc")) is None
    assert not list(world.sessions.glob("*.restoring"))
    # The copy is untouched: with the right key it still restores.
    monkeypatch.setattr(settings.session_vault, "key", right_key)
    world.faults.clear()
    await rw.restore_flow()
    assert read_key(world.file("acc")) == OLD


@pytest.mark.asyncio
async def test_a_restore_over_a_file_that_holds_a_key_is_refused_untouched(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    await rw.start(world, monkeypatch, initial="missing")
    write_session_file(world.file("acc"), key=b"another-live-key")

    await world.run(rw.restore_flow)

    assert "session_restore_file_present" in str(world.outcome)
    assert read_key(world.file("acc")) == b"another-live-key"
    quarantine = settings.session_vault.quarantine_dir
    assert not quarantine.exists() or not list(quarantine.glob("*"))
