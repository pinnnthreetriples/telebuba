"""The deleted-message check of a chat that changed hands: ids read where they are the chat's."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories import chat_broadcast as repository
from services.chat_broadcast import _moves, _seams
from services.chat_broadcast._context import load_context
from tests.services.chat_broadcast.fakes import run_to_end, seed

if TYPE_CHECKING:
    from schemas.telegram_actions_chat import ChatKind
    from tests.services.chat_broadcast.fakes import FakeTelegram

pytestmark = pytest.mark.usefixtures("no_rebalance")


async def _round_one_by_a1_then_a2_takes_over(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch, kind: ChatKind
) -> str:
    """a1 writes round 1 (its message is deleted); while the chat rests it goes to a2."""
    campaign_id = await seed(
        accounts=("a1", "a2"),
        targets=("@alpha",),
        messages=("Hi",),
        loop=True,
        rounds=2,
        rest_minutes={"min": 1, "max": 1},
    )
    telegram.kinds["alpha"] = kind
    telegram.members.add(("a1", "alpha"))
    telegram.deleted.add(101)
    sleep = _seams.sleep
    handed: list[bool] = []

    async def hand_over_while_it_rests(seconds: float) -> None:
        await sleep(seconds)
        target = await repository.fetch_target(campaign_id, "alpha")
        if not handed and target is not None and target.state == "round_done":
            handed.append(False)  # claimed before the awaits: the other sleepers come here too
            ctx = await load_context(campaign_id, "move")
            assert ctx is not None
            handed[0] = await _moves.assign(
                ctx, target, "a2", expect_states=_moves.REBALANCE_STATES, record_origin=False
            )

    monkeypatch.setattr(_seams, "sleep", hand_over_while_it_rests)
    await run_to_end(campaign_id)
    assert handed == [True]
    return campaign_id


@pytest.mark.asyncio
async def test_a_deleted_message_is_found_by_the_account_a_chat_was_handed_to(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await _round_one_by_a1_then_a2_takes_over(telegram, monkeypatch, "megagroup")

    # Message ids belong to a megagroup: a2 reads a1's and sees the admin removed it.
    [alpha] = await repository.list_targets(campaign_id)
    assert (alpha.state, alpha.skip_reason, alpha.chat_kind) == ("skipped", "deleted", "megagroup")
    assert [s.account_id for s in telegram.sent] == ["a1"]


@pytest.mark.asyncio
async def test_a_basic_groups_ids_of_another_account_are_not_read_as_deleted(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await _round_one_by_a1_then_a2_takes_over(telegram, monkeypatch, "basic_group")

    # Ids there are per account: a1's would name other messages in a2's view. Nothing is
    # claimed deleted, and the chat goes on in round 2.
    [alpha] = await repository.list_targets(campaign_id)
    assert (alpha.state, alpha.round, alpha.chat_kind) == ("done", 2, "basic_group")
    assert [s.account_id for s in telegram.sent] == ["a1", "a2"]
