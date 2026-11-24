"""The background repair spaces its tries at a stubborn leftover; an operator's retry does not."""

from __future__ import annotations

import pytest

from core.repositories.account_key_intents import list_open_intents
from services.accounts import _key_repair
from services.accounts._key_repair import repair_key_intents
from tests.services.accounts import test_key_repair as base
from tests.services.accounts.test_key_repair import _NEW, _OLD, _rekey_intent, _sessions, _World
from tests.session_files import write_session_file

world = base.world
_account = base._account


async def _leftover_that_will_not_end(world: _World) -> None:
    write_session_file(_sessions() / "acc.session", key=_OLD)
    write_session_file(_sessions() / "acc_rekey.session", key=_NEW)
    await _rekey_intent()
    world.ended = False


@pytest.mark.asyncio
async def test_the_background_pass_does_not_try_an_unended_leftover_every_pass(
    world: _World,
) -> None:
    await _leftover_that_will_not_end(world)

    assert await repair_key_intents() == 0
    assert await repair_key_intents() == 0

    assert len(world.end_calls) == 1
    assert len(await list_open_intents("acc")) == 1


@pytest.mark.asyncio
async def test_the_wait_doubles_and_an_operators_retry_ignores_it(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _leftover_that_will_not_end(world)
    clock = [1000.0]
    monkeypatch.setattr(_key_repair.time, "monotonic", lambda: clock[0])

    await repair_key_intents()
    clock[0] += 899
    await repair_key_intents()
    assert len(world.end_calls) == 1
    clock[0] += 2
    await repair_key_intents()
    assert len(world.end_calls) == 2
    clock[0] += 901  # the second wait is 30 minutes
    await repair_key_intents()
    assert len(world.end_calls) == 2

    # The operator's own retry of this account always tries, and a success ends the waiting.
    world.ended = True
    assert await repair_key_intents("acc") == 1
    assert len(world.end_calls) == 3
    assert _key_repair._RETRY == {}
