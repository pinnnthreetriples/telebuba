"""Local fixtures for neuroshilling service tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import configure_database
from services import _account_owner
from services.neuroshilling import _runtime, _state, _steps

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def isolate_neuroshilling(tmp_path: Path) -> Iterator[None]:
    """A fresh database and empty process state for every test.

    All four are process-global. The registry decides what the board reports as busy,
    the run generations decide which coroutine is still allowed to act, and the quota
    locks and task map both key on ids a previous test may have used — so a leftover
    from an earlier case would make the next one's verdict depend on execution order.
    The pacer is reset suite-wide by the root conftest, so it is not repeated here.
    """
    configure_database(tmp_path / "telebuba.db")
    _reset()
    yield
    _reset()


def _reset() -> None:
    _account_owner.reset_for_tests()
    _state.reset_for_tests()
    _steps.reset_for_tests()
    _runtime.reset_for_tests()


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record every pause the engine asks for instead of serving it.

    The step delays, the pause between targets and the post-join settle wait are all
    minutes long by default, so a test that actually slept them would not run.
    """
    waited: list[float] = []

    async def _sleep(seconds: float) -> None:
        waited.append(seconds)

    monkeypatch.setattr("services.neuroshilling._seams.sleep", _sleep)
    # Two names for one gate: ``_seams`` imported it directly, ``_telegram`` reaches it
    # through the module. Patching one leaves the other spacing sends 30s apart.
    monkeypatch.setattr("services.neuroshilling._seams.await_send_slot", _slot)
    monkeypatch.setattr("services.pacing.await_send_slot", _slot)
    return waited


async def _slot(*_args: object) -> None:
    """Stand in for the per-account pacer, which otherwise spaces sends 30s apart."""
