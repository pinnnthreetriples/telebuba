"""Reserve accounts: who earns one, who does not, and what the stand-in has to do first.

The economics are the whole subject. A reserve account is spent for good, so it is worth
spending only on a refusal that belongs to the ACCOUNT — a ban, a dead session. Every
refusal that belongs to the CHAT meets the substitute identically, and burning the pool
on one read-only chat is the failure mode these tests exist to pin.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from core.db import _get_engine
from core.repositories.neuroshilling import (
    claim_message,
    count_substitutions,
    list_campaign_accounts,
)
from core.repositories.neuroshilling._tables import _neuroshilling_messages
from schemas.neuroshilling import NeuroshillingStepKey
from schemas.neuroshilling_scenario import NeuroshillingStepInput
from schemas.telegram_actions import ResolveChatResult
from services.neuroshilling import _seams, _telegram, campaigns, engine
from tests.services.neuroshilling.helpers import refused, seed_campaign, sent

if TYPE_CHECKING:
    from schemas.neuroshilling import NeuroshillingCampaignAccount
    from schemas.telegram_actions import ActionResult, TelegramAction

_RUN = "run-1"
_BANNED = refused("failed", error_type="UserBannedInChannelError")
_DEAD = refused("failed", error_message="session_dead")


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


async def _roster(campaign_id: str) -> dict[str, NeuroshillingCampaignAccount]:
    return {row.account_id: row for row in await list_campaign_accounts(campaign_id)}


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


def _message(text: str) -> NeuroshillingStepInput:
    """One line of the first role, said without a pause in front of it."""
    return NeuroshillingStepInput(
        role_id="#0",
        text=text,
        delay_min_seconds=0,
        delay_max_seconds=0,
    )


def _solo_steps(count: int) -> list[NeuroshillingStepInput]:
    return [_message(f"line {index}") for index in range(count)]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_a_banned_account_is_replaced_from_the_pool(answers: list[ActionResult]) -> None:
    """The reserve takes the role, the roster records both halves, and the run goes on."""
    answers.append(_BANNED)
    seeded = await seed_campaign(
        accounts=("acc-1",),
        reserves=("res-1",),
        steps=_solo_steps(2),
    )

    await engine.run_campaign(seeded.campaign_id, _RUN)

    roster = await _roster(seeded.campaign_id)
    assert roster["acc-1"].state == "banned"
    assert roster["res-1"].is_reserve is False
    assert roster["res-1"].role_id == seeded.roles[0].role_id
    assert await _rows() == [("alpha", "res-1", "sent"), ("alpha", "res-1", "sent")]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_the_retry_updates_the_row_instead_of_inserting(
    answers: list[ActionResult],
) -> None:
    """``(run_id, target, step_id)`` is unique, so the failed row is handed over.

    An INSERT would collide with the row the banned account already left behind, and the
    line the ban ate would be lost for the whole run.
    """
    answers.append(_BANNED)
    seeded = await seed_campaign(accounts=("acc-1",), reserves=("res-1",), steps=_solo_steps(1))

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert await _rows() == [("alpha", "res-1", "sent")]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_a_write_forbidden_chat_does_not_burn_the_reserve(
    answers: list[ActionResult],
) -> None:
    """The chat is read-only, so the substitute would hit the identical wall."""
    answers.append(refused("failed", error_type="ChatWriteForbiddenError"))
    seeded = await seed_campaign(accounts=("acc-1",), reserves=("res-1",), steps=_solo_steps(2))

    await engine.run_campaign(seeded.campaign_id, _RUN)

    roster = await _roster(seeded.campaign_id)
    assert roster["acc-1"].state == "active"
    assert roster["res-1"].is_reserve is True
    assert await _rows() == [("alpha", "acc-1", "failed")]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_a_not_participant_failure_does_not_burn_the_reserve(
    answers: list[ActionResult],
) -> None:
    """Not being in the chat says nothing about the account: the next step goes on."""
    answers.append(refused("failed", error_type="UserNotParticipantError"))
    seeded = await seed_campaign(accounts=("acc-1",), reserves=("res-1",), steps=_solo_steps(2))

    await engine.run_campaign(seeded.campaign_id, _RUN)

    roster = await _roster(seeded.campaign_id)
    assert roster["res-1"].is_reserve is True
    assert await _rows() == [("alpha", "acc-1", "failed"), ("alpha", "acc-1", "sent")]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_the_substitute_joins_settles_then_retries(
    monkeypatch: pytest.MonkeyPatch,
    answers: list[ActionResult],
) -> None:
    """It has never been in that chat, and it does not broadcast the second it walks in.

    The pause is not optional decoration: the pacer cannot supply it either, because a
    brand-new reserve account has no send history to be spaced against.
    """
    trace: list[str] = []

    async def _joins(_campaign_id: str, account_id: str, _target: str) -> str:
        trace.append(f"join:{account_id}")
        return "joined"

    async def _execute(account_id: str, _action: TelegramAction) -> ActionResult:
        trace.append(f"send:{account_id}")
        return answers.pop(0) if answers else sent(101)

    async def _sleep(seconds: float) -> None:
        if seconds:
            trace.append("settle")

    monkeypatch.setattr(_telegram, "join_target", _joins)
    monkeypatch.setattr(_seams, "execute", _execute)
    monkeypatch.setattr(_seams, "sleep", _sleep)
    answers.append(_BANNED)
    seeded = await seed_campaign(accounts=("acc-1",), reserves=("res-1",), steps=_solo_steps(1))

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert trace == ["join:acc-1", "settle", "send:acc-1", "join:res-1", "settle", "send:res-1"]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_the_stand_in_replays_its_line_unattached(
    monkeypatch: pytest.MonkeyPatch,
    answers: list[ActionResult],
) -> None:
    """A freshly joined member may not be shown the history the anchor lives in.

    Telegram answers ``MESSAGE_ID_INVALID`` to a reply aimed at a message it will not
    show, and that class has no verdict of its own — the line would vanish as a generic
    failure. Unattached is the same degradation a lost anchor already gets.
    """
    replies: list[int | None] = []

    async def _execute(_account_id: str, action: TelegramAction) -> ActionResult:
        replies.append(getattr(action, "reply_to", None))
        return answers.pop(0) if answers else sent(101)

    monkeypatch.setattr(_seams, "execute", _execute)
    answers.extend([sent(101), _BANNED])
    seeded = await seed_campaign(accounts=("acc-1", "acc-2"), reserves=("res-1",))

    await engine.run_campaign(seeded.campaign_id, _RUN)

    # The opening line answers nothing, the second aims at it, and the replay does not.
    assert replies == [None, 101, None]
    assert await _rows() == [("alpha", "acc-1", "sent"), ("alpha", "res-1", "sent")]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_the_off_switch_spends_no_reserve(answers: list[ActionResult]) -> None:
    """``reserve_enabled`` is the operator's switch, and off means off.

    The pool is untouched — and the ban is still on the roster row, because that is
    Telegram's verdict rather than a setting. Written only by the substitution, it was
    lost with the switch off, and the next run read the account back off the roster as
    ``active`` and dealt it lines again.
    """
    answers.append(_BANNED)
    seeded = await seed_campaign(
        accounts=("acc-1",),
        reserves=("res-1",),
        steps=_solo_steps(2),
        reserve_enabled=False,
    )

    await engine.run_campaign(seeded.campaign_id, _RUN)

    roster = await _roster(seeded.campaign_id)
    assert roster["acc-1"].state == "banned"
    assert await count_substitutions(seeded.campaign_id) == 0
    assert roster["res-1"].is_reserve is True
    assert await _rows() == [("alpha", "acc-1", "failed")]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_a_chat_that_bans_everyone_costs_one_reserve_and_not_one_per_player(
    answers: list[ActionResult],
) -> None:
    """The second ban in one chat is about the CHAT, whatever Telegram called it.

    A correlated fleet condition — one proxy subnet, one registration batch — reaches
    us as ``account_banned`` per account, which is not a verdict that loses the target
    on its own. Without a per-chat count every player of every role buys its own
    reserve and one hostile chat empties the pool.
    """
    answers.extend([_BANNED, sent(101), _BANNED])
    seeded = await seed_campaign(accounts=("acc-1", "acc-2"), reserves=("res-1", "res-2"))

    await engine.run_campaign(seeded.campaign_id, _RUN)

    roster = await _roster(seeded.campaign_id)
    # The first ban bought res-1; the second was refused a reserve and lost the target.
    assert (roster["res-1"].is_reserve, roster["res-2"].is_reserve) == (False, True)
    # Refused a reserve, and still banned: the account is finished either way.
    assert roster["acc-2"].state == "banned"
    assert await count_substitutions(seeded.campaign_id) == 1
    assert await _rows() == [("alpha", "res-1", "sent"), ("alpha", "acc-2", "failed")]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_two_dead_sessions_in_one_chat_are_not_evidence_about_the_chat(
    answers: list[ActionResult],
) -> None:
    """Logging out is not something a chat does, so it must not abandon one.

    Both verdicts buy a substitute — somebody has to say the line either way — but only
    ``account_banned`` is counted per chat. Counting a dead session there made two
    logged-out sessions read as "this chat bans the fleet" and lost a target that had
    refused nothing.
    """
    answers.extend([_DEAD, sent(101), _DEAD])
    seeded = await seed_campaign(accounts=("acc-1", "acc-2"), reserves=("res-1", "res-2"))

    await engine.run_campaign(seeded.campaign_id, _RUN)

    roster = await _roster(seeded.campaign_id)
    # Both reserves were spent, and the second line was said rather than abandoned.
    assert (roster["res-1"].is_reserve, roster["res-2"].is_reserve) == (False, False)
    assert await _rows() == [("alpha", "res-1", "sent"), ("alpha", "res-2", "sent")]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_a_stand_in_banned_on_its_own_replay_is_the_second_ban(
    answers: list[ActionResult],
) -> None:
    """It counts as this chat's second ban, so the target is lost and the chain stops.

    Also the one path that used to leave an account sitting in ``RunContext.banned``
    with nothing ever reading it out again.
    """
    answers.extend([_BANNED, _BANNED])
    seeded = await seed_campaign(
        accounts=("acc-1", "acc-2"),
        reserves=("res-1", "res-2"),
    )

    await engine.run_campaign(seeded.campaign_id, _RUN)

    roster = await _roster(seeded.campaign_id)
    assert (roster["res-1"].is_reserve, roster["res-2"].is_reserve) == (False, True)
    # acc-2's line is never attempted: the target was abandoned on the stand-in's ban.
    assert await _rows() == [("alpha", "res-1", "failed")]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_a_substitute_that_cannot_join_sends_nothing(
    monkeypatch: pytest.MonkeyPatch,
    answers: list[ActionResult],
) -> None:
    """A refused join fails the substitution; the step is not sent from outside the chat."""
    sends: list[str] = []

    async def _joins(_campaign_id: str, account_id: str, _target: str) -> str:
        return "joined" if account_id == "acc-1" else "refused"

    async def _execute(account_id: str, _action: TelegramAction) -> ActionResult:
        sends.append(account_id)
        return answers.pop(0) if answers else sent(101)

    monkeypatch.setattr(_telegram, "join_target", _joins)
    monkeypatch.setattr(_seams, "execute", _execute)
    answers.append(_BANNED)
    seeded = await seed_campaign(accounts=("acc-1",), reserves=("res-1",), steps=_solo_steps(2))

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert sends == ["acc-1"]
    # The roster swap already happened, so the stand-in plays the NEXT target; only
    # this one is abandoned.
    assert (await _roster(seeded.campaign_id))["res-1"].is_reserve is False
    assert await _rows() == [("alpha", "acc-1", "failed")]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_a_flooded_substitute_leaves_the_run(
    monkeypatch: pytest.MonkeyPatch,
    answers: list[ActionResult],
) -> None:
    """A flood on the stand-in's join is an ACCOUNT verdict: it plays no target at all."""

    async def _joins(_campaign_id: str, account_id: str, _target: str) -> str:
        return "joined" if account_id == "acc-1" else "flooded"

    monkeypatch.setattr(_telegram, "join_target", _joins)
    answers.append(_BANNED)
    seeded = await seed_campaign(
        targets="@alpha @beta",
        accounts=("acc-1",),
        reserves=("res-1",),
        steps=_solo_steps(1),
    )

    await engine.run_campaign(seeded.campaign_id, _RUN)

    # Both accounts halted, so the walk stops rather than paying the pause to reach
    # another target with nobody to speak in it.
    assert await _rows() == [("alpha", "acc-1", "failed")]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_the_stand_in_plays_the_targets_after_the_one_it_was_bought_for(
    answers: list[ActionResult],
) -> None:
    """A substitution that WORKED must not be what ends the run.

    ``_swap_roster`` puts the stand-in into ``context.by_role``, so the cast has to be
    read from there per target: taken once before the walk, the list went on naming the
    banned account it had replaced — which is halted — so "everybody is halted" came
    true at the next target and the walk stopped. One healthy role, one ban, and every
    remaining chat was abandoned.
    """
    answers.append(_BANNED)
    seeded = await seed_campaign(
        targets="@alpha @beta",
        accounts=("acc-1",),
        reserves=("res-1",),
        steps=_solo_steps(1),
    )

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert await _rows() == [("alpha", "res-1", "sent"), ("beta", "res-1", "sent")]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_run_continues_when_the_pool_is_empty(answers: list[ActionResult]) -> None:
    """No reserve loses the target the ban happened in, and nothing beyond it."""
    answers.extend([sent(101), _BANNED])
    seeded = await seed_campaign(targets="@alpha @beta", accounts=("acc-1", "acc-2"))

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert await _rows() == [
        ("alpha", "acc-1", "sent"),
        ("alpha", "acc-2", "failed"),
        ("beta", "acc-1", "sent"),
    ]
    assert (await _roster(seeded.campaign_id))["acc-2"].state == "banned"


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_a_reaction_is_not_replayed_but_still_costs_the_reserve(
    answers: list[ActionResult],
) -> None:
    """The stand-in joins and takes the role; the reaction itself is left where it fell."""
    answers.extend([sent(101), _BANNED])
    seeded = await seed_campaign(
        accounts=("acc-1",),
        reserves=("res-1",),
        steps=[
            _message("opening"),
            NeuroshillingStepInput(
                role_id="#0",
                kind="reaction",
                emoji="🔥",
                target_position=1,
                delay_min_seconds=0,
                delay_max_seconds=0,
            ),
            _message("closing"),
        ],
    )

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert (await _roster(seeded.campaign_id))["res-1"].role_id == seeded.roles[0].role_id
    assert await _rows() == [
        ("alpha", "acc-1", "sent"),
        ("alpha", "acc-1", "failed"),
        ("alpha", "res-1", "sent"),
    ]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_the_stand_in_is_refused_by_its_own_quota(answers: list[ActionResult]) -> None:
    """The ceilings belong to the ACCOUNT, so the replay re-counts them for the new one.

    Handing a row over moves it from one account's hourly tally to another's, and a
    reserve account is not necessarily idle — it is a Telegram session with a history
    of its own.
    """
    answers.append(_BANNED)
    seeded = await seed_campaign(
        accounts=("acc-1",),
        reserves=("res-1",),
        steps=_solo_steps(1),
        messages_per_hour=1,
    )
    # An hour of `res-1`'s own history, in another run of the same campaign.
    await claim_message(
        NeuroshillingStepKey(run_id="earlier", target="zulu", step_id=seeded.steps[0].step_id),
        campaign_id=seeded.campaign_id,
        account_id="res-1",
        text="spent",
    )

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert await _rows() == [("zulu", "res-1", "pending"), ("alpha", "acc-1", "failed")]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_a_row_that_is_no_longer_failed_is_not_sent_again(
    monkeypatch: pytest.MonkeyPatch,
    answers: list[ActionResult],
) -> None:
    """No row, no send: the journal entry is what reserves the step, replay included."""
    sends: list[str] = []

    async def _no_row(*_args: object, **_kwargs: object) -> bool:
        return False

    async def _execute(account_id: str, _action: TelegramAction) -> ActionResult:
        sends.append(account_id)
        return answers.pop(0) if answers else sent(101)

    monkeypatch.setattr("core.repositories.neuroshilling.hand_over_message", _no_row)
    monkeypatch.setattr(_seams, "execute", _execute)
    answers.append(_BANNED)
    seeded = await seed_campaign(accounts=("acc-1",), reserves=("res-1",), steps=_solo_steps(1))

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert sends == ["acc-1"]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_the_launch_card_counts_the_substitution(answers: list[ActionResult]) -> None:
    """Counted off ``replaced_by_account_id``, so an empty pool does not inflate it."""
    answers.append(_BANNED)
    seeded = await seed_campaign(accounts=("acc-1",), reserves=("res-1",), steps=_solo_steps(1))

    before = await campaigns.run_status(seeded.campaign_id)
    await engine.run_campaign(seeded.campaign_id, _RUN)
    after = await campaigns.run_status(seeded.campaign_id)

    assert before is not None
    assert after is not None
    assert (before.substitutions, after.substitutions) == (0, 1)
