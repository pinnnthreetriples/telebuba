"""A chat the operator handed over by hand stays put; a chat keeps the FIRST account it left."""

from __future__ import annotations

import pytest

from core.repositories import chat_broadcast as repository
from schemas.chat_broadcast import ChatBroadcastTargetAction
from services import chat_broadcast as service
from services.chat_broadcast import _moves, _schedule
from tests.services.chat_broadcast.layout import chat, setup


async def _hand(campaign_id: str, key: str, account: str) -> None:
    await service.act_on_target(
        campaign_id, ChatBroadcastTargetAction(chat_key=key, action="hand", account_id=account)
    )


async def _target(campaign_id: str, key: str) -> tuple[str | None, bool, str | None]:
    target = await repository.fetch_target(campaign_id, key)
    assert target is not None
    return target.assigned_account_id, target.pinned, target.handed_from_account_id


@pytest.mark.asyncio
async def test_a_chat_handed_over_by_hand_is_not_balanced_away_on_the_next_ticks() -> None:
    # a1 has one chat it is inside; a2 has three. The operator gives a1's to a2: loads 0 / 4.
    chats = [chat("mine", "a1", "a1")] + [chat(f"busy{index}", "a2", "a2") for index in range(3)]
    campaign_id, ctx = await setup(("a1", "a2"), chats)

    await _hand(campaign_id, "mine", "a2")
    for _pass in range(2):
        await _schedule.tick(ctx, force=True)

    # Without the pin the balance sends it straight back (it is the cheapest for a1).
    assert await _target(campaign_id, "mine") == ("a2", True, "a1")
    load = _moves.open_load(await repository.list_targets(campaign_id))
    assert (load["a1"], load["a2"]) == (2, 2)


@pytest.mark.asyncio
async def test_a_pinned_chat_is_the_only_one_in_the_way_the_rest_still_balance() -> None:
    chats = [chat(f"chat{index}", "a1", "a1") for index in range(4)]
    campaign_id, ctx = await setup(("a1", "a2"), chats)
    for key in ("chat0", "chat1", "chat2", "chat3"):
        await repository.update_target(campaign_id, key, pinned=1)

    # Every chat is pinned to a1: the gap is four and nothing may move.
    assert await _schedule.rebalance(ctx, ["a1", "a2"]) == 0

    await repository.update_target(campaign_id, "chat3", pinned=0)
    assert await _schedule.rebalance(ctx, ["a1", "a2"]) == 1
    assert (await _target(campaign_id, "chat3"))[0] == "a2"


@pytest.mark.asyncio
async def test_a_rest_or_a_halt_still_moves_a_pinned_chat_and_ends_the_pin() -> None:
    campaign_id, ctx = await setup(("a1", "a2", "a3"), [chat("x", "a1", "a1")])
    await _hand(campaign_id, "x", "a2")
    assert (await _target(campaign_id, "x"))[:2] == ("a2", True)

    # a2 rests for hours: the engine moves the chat for that reason, the pin is spent.
    assert await _moves.hand_over(ctx, "a2", ["a1", "a3"]) == 1

    owner, pinned, _origin = await _target(campaign_id, "x")
    assert owner == "a1"
    assert not pinned


@pytest.mark.asyncio
async def test_a_chat_moved_several_times_keeps_the_account_it_first_left() -> None:
    campaign_id, ctx = await setup(("a1", "a2", "a3", "a4"), [chat("x", "a1", "a1")])

    await _moves.hand_over(ctx, "a1", ["a2"])
    assert await _target(campaign_id, "x") == ("a2", False, "a1")
    await _moves.hand_over(ctx, "a2", ["a3"])
    assert await _target(campaign_id, "x") == ("a3", False, "a1")
    await _hand(campaign_id, "x", "a4")

    # Three moves later it still says where it started.
    assert await _target(campaign_id, "x") == ("a4", True, "a1")


@pytest.mark.asyncio
async def test_a_chat_the_balance_moved_gets_its_origin_from_the_first_move_that_counts() -> None:
    campaign_id, ctx = await setup(("a1", "a2", "a3"), [chat("x", "a1", "a1")])
    target = await repository.fetch_target(campaign_id, "x")
    assert target is not None

    # A balancing move records nothing; the hand-over after it records where it stood then.
    assert await _moves.assign(ctx, target, "a2", record_origin=False)
    assert (await _target(campaign_id, "x"))[2] is None
    await _moves.hand_over(ctx, "a2", ["a3"])

    assert await _target(campaign_id, "x") == ("a3", False, "a2")
