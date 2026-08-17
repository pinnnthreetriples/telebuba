"""«Оживление чата»: no joins, no denominator, and a fresh journal key per cycle."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from core.db import _get_engine
from core.repositories.neuroshilling._tables import _neuroshilling_messages
from schemas.telegram_actions import PostComment, ResolveChatResult
from services.neuroshilling import _revive, _seams, _telegram, engine
from services.neuroshilling.campaigns import run_status
from tests.services.neuroshilling.helpers import refused, seed_campaign, sent

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
        self.answers: list[ActionResult] = []
        self.cycle_pauses = 0

    async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
        self.sends.append((account_id, action))
        if self.answers:
            return self.answers.pop(0)
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


def _published(wiring: _Wiring) -> list[str]:
    """The text of every message that actually reached the gateway, in order."""
    return [action.text for _account_id, action in wiring.sends if isinstance(action, PostComment)]


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
async def test_every_cycle_says_the_whole_dialogue_again(wiring: _Wiring) -> None:
    """Two gates key on the very thing a cycle repeats, and neither may swallow it.

    The journal is unique on ``(run_id, target, step_id)`` — exactly right for a
    campaign, which must never play a step into a chat twice — and the dedup store
    holds a chat's texts for a week. A cycle reusing either key posts nothing. So the
    cycle number is a suffix on the journal key AND on the dedup reservation, and on
    neither the campaign's ``run_id``, which stays the identity Stop settles against.

    Asserted on what went OUT and not on the rows: ``claim_message`` writes its row
    before the content gate is consulted at all, so a cycle that published nothing
    still leaves a full set of rows behind and a row count cannot tell the two apart.
    """
    seeded = await seed_campaign(mode="revive", targets="@mychat")

    with pytest.raises(_StoppedError):
        await engine.run_campaign(seeded.campaign_id, _RUN)

    dialogue = [step.text for step in seeded.steps]
    assert _published(wiring) == dialogue * wiring.cycles
    assert set(await _journal_run_ids()) == {f"{_RUN}#1", f"{_RUN}#2"}


@pytest.mark.asyncio
async def test_a_resumed_run_counts_its_cycles_on_from_the_journal(wiring: _Wiring) -> None:
    """A cycle number that restarted at zero would replay into keys already taken.

    ``claim_message`` refuses each of them and the step counts as played, so every
    cycle the killed process had got through is paid for again in step delays, the
    listening window and the pause between cycles — and says nothing.
    """
    # The per-chat daily ceiling is a campaign's, and this run puts four cycles into
    # one chat; 0 is the mode's own answer to a dialogue that repeats all day.
    seeded = await seed_campaign(
        mode="revive",
        targets="@mychat",
        messages_per_chat_per_day=0,
    )
    with pytest.raises(_StoppedError):
        await engine.run_campaign(seeded.campaign_id, _RUN)
    # The loop's stop condition lives on the fixture, not in the run's own state.
    wiring.cycle_pauses = 0

    with pytest.raises(_StoppedError):
        await engine.run_campaign(seeded.campaign_id, _RUN)

    dialogue = [step.text for step in seeded.steps]
    assert _published(wiring) == dialogue * wiring.cycles * 2
    assert set(await _journal_run_ids()) == {f"{_RUN}#{cycle}" for cycle in (1, 2, 3, 4)}


@pytest.mark.asyncio
async def test_the_loop_ends_when_every_speaker_has_been_halted(wiring: _Wiring) -> None:
    """The one exit that is not cancellation — asserted by NOT needing the fixture.

    Every other case here has to raise its way out of a loop that never ends. A
    flood is a verdict on the SESSION, so it takes the account out of the run for
    good; once the last one holding a resolved chat has gone, nobody is left to say
    a line and the loop stops on its own. ``chat_blocked`` is deliberately not this
    — it ends the step loop for one cycle and halts nobody — so a target the fleet
    lost keeps being acted in, which is what ``_revive.run`` now says it does.
    """
    seeded = await seed_campaign(mode="revive", targets="@mychat")
    wiring.answers = [refused("flood_wait", flood_wait_seconds=60) for _ in seeded.steps]

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert len(wiring.sends) == len(seeded.steps)


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
