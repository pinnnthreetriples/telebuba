"""What the engine does when Telegram says stop — and how far each refusal reaches.

The rate-limit family arrives as a STATUS with no ``error_type`` at all, so an engine
that switched on the error class would see an empty field, file the step as an ordinary
failure, and go on posting after Telegram refused it. The split that matters is scope:
a flood belongs to the ACCOUNT and takes it out of the whole run, slow mode belongs to
the CHAT and costs one step, and a write-forbidden chat costs the target.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from core.db import _get_engine
from core.repositories.neuroshilling import list_presence
from core.repositories.neuroshilling._tables import _neuroshilling_messages
from schemas.neuroshilling_scenario import NeuroshillingStepInput
from schemas.telegram_actions import ResolveChatResult
from services.neuroshilling import _seams, _telegram, engine
from tests.services.neuroshilling.helpers import refused, seed_campaign, sent

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult, TelegramAction

_RUN = "run-1"


async def _rows() -> list[tuple[str, str, str]]:
    def _read() -> list[tuple[str, str, str]]:
        statement = select(
            _neuroshilling_messages.c.target,
            _neuroshilling_messages.c.account_id,
            _neuroshilling_messages.c.status,
        ).order_by(_neuroshilling_messages.c.id)
        with _get_engine().connect() as connection:
            return [(str(a), str(b), str(c)) for a, b, c in connection.execute(statement)]

    return await asyncio.to_thread(_read)


@pytest.fixture
def answers(monkeypatch: pytest.MonkeyPatch) -> list[ActionResult]:
    """Queue of gateway answers; anything past the end is a plain delivery."""
    queued: list[ActionResult] = []
    count = 0

    async def _execute(_account_id: str, _action: TelegramAction) -> ActionResult:
        nonlocal count
        count += 1
        return queued.pop(0) if queued else sent(100 + count)

    async def _resolve(_account_id: str, _action: TelegramAction) -> ResolveChatResult:
        return ResolveChatResult(chat_id=555, kind="megagroup")

    async def _joins(_campaign_id: str, _account_id: str, _target: str) -> str:
        return "joined"

    monkeypatch.setattr(_seams, "execute", _execute)
    monkeypatch.setattr(_seams, "execute_read", _resolve)
    monkeypatch.setattr(_telegram, "join_target", _joins)
    return queued


def _solo_steps(count: int) -> list[NeuroshillingStepInput]:
    return [
        NeuroshillingStepInput(
            role_id="#0",
            text=f"line {index}",
            delay_min_seconds=0,
            delay_max_seconds=0,
        )
        for index in range(count)
    ]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_a_flood_wait_halts_the_account_for_the_whole_run(
    answers: list[ActionResult],
) -> None:
    """One flood in one chat is a verdict about the account, not about that chat.

    The account plays nothing more — in this target or the next — and the verdict is
    persisted across its presence rows, so a restart does not resume it inside its own
    flood window.
    """
    answers.append(refused("flood_wait", flood_wait_seconds=60))
    seeded = await seed_campaign(targets="@alpha @beta", accounts=("acc-1",), steps=_solo_steps(2))

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert await _rows() == [("alpha", "acc-1", "failed")]
    stored = await list_presence(seeded.campaign_id)
    assert {row.state for row in stored} == {"flooded"}


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_a_slow_mode_wait_only_skips_the_step(answers: list[ActionResult]) -> None:
    """The chat is pacing us, not the account: the next step goes on as usual."""
    answers.append(refused("slow_mode_wait", flood_wait_seconds=10))
    seeded = await seed_campaign(accounts=("acc-1",), steps=_solo_steps(2))

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert await _rows() == [("alpha", "acc-1", "skipped"), ("alpha", "acc-1", "sent")]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_a_write_forbidden_chat_loses_the_target_and_no_more(
    answers: list[ActionResult],
) -> None:
    """A read-only chat refuses the substitute exactly as it refused the first."""
    answers.append(refused("failed", error_type="ChatWriteForbiddenError"))
    seeded = await seed_campaign(targets="@alpha @beta", accounts=("acc-1",), steps=_solo_steps(2))

    await engine.run_campaign(seeded.campaign_id, _RUN)

    rows = await _rows()
    assert rows[0] == ("alpha", "acc-1", "failed")
    assert [row[0] for row in rows[1:]] == ["beta", "beta"]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_an_account_flooded_on_the_join_never_speaks(
    monkeypatch: pytest.MonkeyPatch,
    answers: list[ActionResult],
) -> None:
    async def _flooded(_campaign_id: str, _account_id: str, _target: str) -> str:
        return "flooded"

    monkeypatch.setattr(_telegram, "join_target", _flooded)
    seeded = await seed_campaign(accounts=("acc-1",), steps=_solo_steps(2))

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert answers == []
    assert await _rows() == []


@pytest.mark.asyncio
async def test_a_run_with_nobody_left_to_speak_stops_walking_the_target_list(
    answers: list[ActionResult],
    no_sleep: list[float],
) -> None:
    """Every remaining target costs a pause to reach the same empty cast.

    The pause between targets is minutes by default, so a fifty-target campaign spent
    a quarter of an hour joining nothing and saying nothing after its last account was
    halted.
    """
    answers.append(refused("flood_wait", flood_wait_seconds=60))
    seeded = await seed_campaign(
        targets="@alpha @beta @gamma",
        accounts=("acc-1",),
        steps=_solo_steps(1),
    )

    await engine.run_campaign(seeded.campaign_id, _RUN)

    # The settle pause after entering alpha and that target's one step delay, and
    # nothing for beta or gamma.
    assert len(no_sleep) == 2
    assert await _rows() == [("alpha", "acc-1", "failed")]
