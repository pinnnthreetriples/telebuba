"""«Оживление чата»: no joins, no denominator, and a fresh journal key per cycle."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from core.db import _get_engine
from core.repositories.neuroshilling._tables import _neuroshilling_messages
from schemas.telegram_actions import ResolveChatResult
from services.neuroshilling import _revive, _seams, _telegram, engine
from services.neuroshilling.campaigns import run_status
from tests.services.neuroshilling.helpers import seed_campaign, sent

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult, TelegramAction, TelegramReadAction

_RUN = "run-1"


class _StoppedError(Exception):
    """Stands in for the cancellation that really ends a revive run."""


class _Wiring:
    """Answers every call, and ends the endless loop after ``cycles`` of it."""

    def __init__(self, cycles: int) -> None:
        self.cycles = cycles
        self.joins: list[tuple[str, str]] = []
        self.sends: list[tuple[str, TelegramAction]] = []
        self.cycle_pauses = 0

    async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
        self.sends.append((account_id, action))
        return sent(100 + len(self.sends))

    async def execute_read(
        self,
        _account_id: str,
        _action: TelegramReadAction,
    ) -> ResolveChatResult:
        return ResolveChatResult(chat_id=555, kind="megagroup")

    async def join(self, _campaign_id: str, account_id: str, target: str) -> str:
        self.joins.append((account_id, target))
        return "joined"

    def cycle_pause(self) -> float:
        """Stand in for the between-cycle wait, and stop the run at the Nth one.

        Hooked HERE and not on ``_seams.sleep``: that one also serves the step
        delays and the settle wait, so counting it would make the stop point depend
        on how many lines the dialogue happens to have. Raising ON the last allowed
        pause rather than after it is what makes ``cycles`` the number of cycles
        that actually played.
        """
        self.cycle_pauses += 1
        if self.cycle_pauses >= self.cycles:
            raise _StoppedError
        return 0.0


async def _journal_run_ids() -> list[str]:
    def _read() -> list[str]:
        with _get_engine().connect() as connection:
            statement = select(_neuroshilling_messages.c.run_id).order_by(
                _neuroshilling_messages.c.id,
            )
            return [str(row[0]) for row in connection.execute(statement)]

    import asyncio  # noqa: PLC0415 - one call, inside the only helper that needs it.

    return await asyncio.to_thread(_read)


@pytest.fixture
def wiring(monkeypatch: pytest.MonkeyPatch, no_sleep: list[float]) -> _Wiring:
    """Every seam the engine reaches out through, plus the loop's stop condition.

    ``no_sleep`` serves the step delays and the settle wait; the between-cycle
    pause is hooked separately, because that is the one this module is about.
    """
    assert no_sleep == []
    fake = _Wiring(cycles=2)
    monkeypatch.setattr(_seams, "execute", fake.execute)
    monkeypatch.setattr(_seams, "execute_read", fake.execute_read)
    monkeypatch.setattr(_telegram, "join_target", fake.join)
    monkeypatch.setattr(_revive, "cycle_pause", fake.cycle_pause)
    return fake


@pytest.mark.asyncio
async def test_a_revive_run_never_joins_the_chat(wiring: _Wiring) -> None:
    """The accounts are already in a chat the operator owns.

    A join there could only answer ``already_participant``, and it would spend a
    slot of the shared daily join budget — the same counter neurocomment's
    onboarding draws on — to learn nothing.
    """
    seeded = await seed_campaign(mode="revive", targets="@mychat")

    with pytest.raises(_StoppedError):
        await engine.run_campaign(seeded.campaign_id, _RUN)

    assert wiring.joins == []
    assert wiring.sends


@pytest.mark.asyncio
async def test_each_cycle_journals_under_a_key_of_its_own(wiring: _Wiring) -> None:
    """The unique index is what makes a campaign safe and what makes revive loop.

    ``(run_id, target, step_id)`` is exactly right for a campaign — a step is never
    played into a chat twice — so a second cycle under the same id would insert
    nothing and post nothing. The cycle number is a suffix on the journal key and
    NOT on the campaign's ``run_id``, which stays the identity Stop settles against.
    """
    seeded = await seed_campaign(mode="revive", targets="@mychat")

    with pytest.raises(_StoppedError):
        await engine.run_campaign(seeded.campaign_id, _RUN)

    run_ids = await _journal_run_ids()

    assert set(run_ids) == {f"{_RUN}#1", f"{_RUN}#2"}
    assert len(run_ids) == wiring.cycles * len(seeded.steps)


@pytest.mark.asyncio
async def test_the_progress_of_a_revive_run_has_no_denominator() -> None:
    """It loops until it is stopped, so there is no amount of work it is part of.

    The launch card shows the count itself; a denominator describing one cycle
    would make a bar that fills up and then keeps going.
    """
    seeded = await seed_campaign(mode="revive", targets="@mychat")

    status = await run_status(seeded.campaign_id)

    assert status is not None
    assert status.total == 0


@pytest.mark.asyncio
async def test_an_ordinary_campaign_still_walks_its_targets_once(wiring: _Wiring) -> None:
    """The mode is a branch, not a rewrite: nothing above changes for a campaign."""
    seeded = await seed_campaign(targets="@alpha")

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert await _journal_run_ids() == [_RUN] * len(seeded.steps)
    # ``parse_targets`` normalises the paste box, so the engine walks bare tokens.
    assert wiring.joins == [("acc-1", "alpha"), ("acc-2", "alpha")]
