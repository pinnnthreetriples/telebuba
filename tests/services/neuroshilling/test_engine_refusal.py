"""A chat that refuses ONE account: who says the line, and what is never played.

The production shape this pins: a four-line dialogue between an asker and an answerer,
where the answerer's LAST line is the one the campaign exists for. An anti-spam bot
mutes the asker after its first message, the engine read that as "the chat is lost",
and the closing line was never sent — while the answerer could write there all along.

Two rules replace that one, and they pull in opposite directions on purpose. A refusal
silences the account, not the chat, so the dialogue runs on to its last line. But a
dialogue that never OPENED is not continued, because an answer to a question nobody
asked is the one thing worse than silence — including on a resumed run, which is
handed exactly the steps that have no journal row yet.
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
from schemas.telegram_actions import PostComment, ResolveChatResult
from services.neuroshilling import _cast, _dispatch, _seams, _steps, _telegram, engine
from tests.services.neuroshilling.helpers import refused, seed_campaign, sent

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult, TelegramAction

_RUN = "run-1"
_WRITE_FORBIDDEN = "ChatWriteForbiddenError"


class _Wiring:
    """A gateway whose answer depends on WHO is writing WHERE, plus what it was asked."""

    def __init__(self) -> None:
        # (account_id, chat_id) pairs the chat refuses, each with the error it answers.
        self.refuses: dict[tuple[str, int], str] = {}
        # account_id -> how many of its messages land before the chat mutes it.
        self.muted_after: dict[str, int] = {}
        self.landed: dict[str, int] = {}
        # (account_id, target) pairs that never get into the chat.
        self.locked_out: set[tuple[str, str]] = set()
        self.sends: list[tuple[str, PostComment]] = []
        self.events: list[str] = []


_CHAT_IDS = {"alpha": 501, "beta": 502}


@pytest.fixture
def wiring(monkeypatch: pytest.MonkeyPatch) -> _Wiring:
    state = _Wiring()

    async def _execute(account_id: str, action: TelegramAction) -> ActionResult:
        assert isinstance(action, PostComment)
        state.sends.append((account_id, action))
        error = state.refuses.get((account_id, action.chat_id))
        if error is not None:
            return refused("failed", error_type=error)
        landed = state.landed.get(account_id, 0)
        if landed >= state.muted_after.get(account_id, landed + 1):
            return refused("failed", error_type=_WRITE_FORBIDDEN)
        state.landed[account_id] = landed + 1
        return sent(100 + len(state.sends))

    async def _resolve(_account_id: str, action: TelegramAction) -> ResolveChatResult:
        return ResolveChatResult(chat_id=_CHAT_IDS[action.target], kind="megagroup")  # ty: ignore[unresolved-attribute]

    async def _joins(_campaign_id: str, account_id: str, target: str) -> str:
        return "refused" if (account_id, target) in state.locked_out else "joined"

    async def _capture(_level: str, event: str, **_fields: object) -> None:
        state.events.append(event)

    monkeypatch.setattr(_seams, "execute", _execute)
    monkeypatch.setattr(_seams, "execute_read", _resolve)
    monkeypatch.setattr(_telegram, "join_target", _joins)
    for module in (engine, _steps, _cast, _dispatch):
        monkeypatch.setattr(module, "log_event", _capture)
    return state


def _line(role: int, text: str, reply_to: int | None = None) -> NeuroshillingStepInput:
    return NeuroshillingStepInput(
        role_id=f"#{role}",
        text=text,
        reply_to_position=reply_to,
        delay_min_seconds=0,
        delay_max_seconds=0,
    )


def _dialogue() -> list[NeuroshillingStepInput]:
    """Asker, answerer, asker, answerer — the closing line is the answerer's."""
    return [
        _line(0, "where do I get one?"),
        _line(1, "for what?", 1),
        _line(0, "the usual", 2),
        _line(1, "ask the bot, that is where I got mine", 3),
    ]


async def _rows(target: str) -> list[tuple[str, str]]:
    """``(account_id, status)`` of every journal row for ``target``, oldest first."""

    def _read() -> list[tuple[str, str]]:
        statement = (
            select(_neuroshilling_messages.c.account_id, _neuroshilling_messages.c.status)
            .where(_neuroshilling_messages.c.target == target)
            .order_by(_neuroshilling_messages.c.id)
        )
        with _get_engine().connect() as connection:
            return [(str(a), str(b)) for a, b in connection.execute(statement)]

    return await asyncio.to_thread(_read)


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_a_refused_account_does_not_cost_the_dialogue_its_closing_line(
    wiring: _Wiring,
) -> None:
    """The asker is muted after its first message; the answerer still closes.

    The closing reply climbs past the lost third line to the answerer's own second
    message, so the thread reads as one conversation rather than a line aimed at nothing.
    """
    wiring.muted_after["acc-1"] = 1
    seeded = await seed_campaign(steps=_dialogue())

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert await _rows("alpha") == [
        ("acc-1", "sent"),
        ("acc-2", "sent"),
        ("acc-1", "failed"),
        ("acc-2", "sent"),
    ]
    account_id, closing = wiring.sends[-1]
    assert (account_id, closing.text) == ("acc-2", "ask the bot, that is where I got mine")
    # Message ids are 100 + the send's ordinal, and the second send was the answerer's.
    assert closing.reply_to == 102
    assert "neuroshilling_account_silenced" in wiring.events
    assert "neuroshilling_target_failed" not in wiring.events


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_a_refusal_is_written_on_the_pair_so_the_next_run_joins_again(
    wiring: _Wiring,
) -> None:
    """The presence row said ``joined`` for an account the chat had already removed."""
    wiring.refuses[("acc-2", 501)] = "ChatGuestSendForbiddenError"
    seeded = await seed_campaign(steps=_dialogue())

    await engine.run_campaign(seeded.campaign_id, _RUN)

    stored = {(row.account_id, row.target): row for row in await list_presence(seeded.campaign_id)}
    assert stored[("acc-2", "alpha")].state == "refused"
    assert stored[("acc-2", "alpha")].last_error_type == "ChatGuestSendForbiddenError"


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_a_role_with_nobody_left_costs_its_own_lines_only(wiring: _Wiring) -> None:
    """The answerer is refused: its two lines are lost, the asker's both go out."""
    wiring.refuses[("acc-2", 501)] = _WRITE_FORBIDDEN
    seeded = await seed_campaign(steps=_dialogue())

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert [(account_id, action.text) for account_id, action in wiring.sends] == [
        ("acc-1", "where do I get one?"),
        ("acc-2", "for what?"),
        ("acc-1", "the usual"),
    ]
    assert wiring.events.count("neuroshilling_role_exhausted") == 2


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_nothing_is_played_on_top_of_a_dialogue_that_never_opened(
    wiring: _Wiring,
) -> None:
    """The asker cannot write in ``alpha``: no answer to nothing, and ``beta`` is played."""
    wiring.refuses[("acc-1", 501)] = _WRITE_FORBIDDEN
    seeded = await seed_campaign(targets="@alpha @beta", steps=_dialogue())

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert await _rows("alpha") == [("acc-1", "failed")]
    assert [status for _account, status in await _rows("beta")] == ["sent"] * 4
    assert wiring.events.count("neuroshilling_dialogue_not_opened") == 1


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_a_resumed_run_does_not_walk_back_into_a_target_from_the_middle(
    wiring: _Wiring,
) -> None:
    """The restart found three unjournalled steps there and played the second one.

    That is what put an answerer's "for what?" into three chats with no question above
    it: the pass had given the target up at its first line, the process restarted, and
    the resume was handed every step but that first.
    """
    wiring.refuses[("acc-1", 501)] = _WRITE_FORBIDDEN
    seeded = await seed_campaign(steps=_dialogue())
    await engine.run_campaign(seeded.campaign_id, _RUN)
    wiring.sends.clear()

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert wiring.sends == []
    assert await _rows("alpha") == [("acc-1", "failed")]


@pytest.mark.asyncio
async def test_a_target_the_opening_role_never_entered_is_skipped_before_any_wait(
    wiring: _Wiring,
    no_sleep: list[float],
) -> None:
    """Found out through the steps, it cost the settle pause and a step delay per chat."""
    wiring.locked_out.add(("acc-1", "alpha"))
    seeded = await seed_campaign(steps=_dialogue())

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert wiring.sends == []
    assert no_sleep == []
    assert wiring.events == ["neuroshilling_target_failed"]
