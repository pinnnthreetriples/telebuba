"""A resting account's chats go to whoever writes there soonest, and no further than it can write.

Seen live: three accounts at 20 an hour passed every chat back and forth each hour — to
accounts that were not inside (a join and the operator's pause after it, longer than the
owner's own rest) and to accounts with one send left in the hour (back again minutes later).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories import chat_broadcast as repository
from schemas.chat_broadcast_records import JournalKey
from services.chat_broadcast import _rotation
from tests.services.chat_broadcast.layout import chat, setup

if TYPE_CHECKING:
    from tests.services.chat_broadcast.fakes import Clock

pytestmark = pytest.mark.usefixtures("no_rebalance")


async def _owners(campaign_id: str) -> dict[str, str | None]:
    return {t.chat_key: t.assigned_account_id for t in await repository.list_targets(campaign_id)}


async def _joined(campaign_id: str, chat_key: str, account_id: str) -> None:
    await repository.add_event(
        campaign_id, chat_key, round_number=1, kind="joined", account_id=account_id
    )


async def _sent(campaign_id: str, account_id: str, count: int) -> None:
    for step in range(count):
        key = JournalKey(run_id="old", chat_key=f"x-{account_id}", round=1, step_index=step)
        assert await repository.claim_message(
            key, campaign_id=campaign_id, account_id=account_id, kind="text"
        )
        await repository.settle_message(key, "sent", text="Hi")


@pytest.mark.asyncio
async def test_a_chat_goes_to_an_account_already_inside_before_an_idler_stranger(
    clock: Clock,
) -> None:
    chats = [chat("chat0", "a1", "a1"), chat("other", "a3", "a3")]
    campaign_id, ctx = await setup(("a1", "a2", "a3"), chats, join_delay_minutes=0)
    # a3 wrote here before the chat was handed back to a1: it is still inside.
    await _joined(campaign_id, "chat0", "a3")
    await repository.rest_account(campaign_id, "a1", clock.now() + 3000, "hour_limit")

    assert await _rotation.place_resting(ctx) == 1

    # a2 has nothing to do, but it would have to join; a3 writes at once.
    assert (await _owners(campaign_id))["chat0"] == "a3"


@pytest.mark.asyncio
async def test_a_stranger_is_not_given_a_chat_its_owner_is_back_in_sooner(clock: Clock) -> None:
    campaign_id, ctx = await setup(("a1", "a2"), [chat("chat0", "a1", "a1")], join_delay_minutes=60)
    await repository.rest_account(campaign_id, "a1", clock.now() + 1800, "hour_limit")

    # Half an hour for a1 against a join and an hour's pause for a2.
    assert await _rotation.place_resting(ctx) == 0
    assert (await _owners(campaign_id))["chat0"] == "a1"


@pytest.mark.asyncio
async def test_a_stranger_takes_the_chat_when_the_owner_is_out_for_longer(clock: Clock) -> None:
    campaign_id, ctx = await setup(("a1", "a2"), [chat("chat0", "a1", "a1")], join_delay_minutes=60)
    await repository.rest_account(campaign_id, "a1", clock.now() + 6 * 3600, "day_limit")

    assert await _rotation.place_resting(ctx) == 1
    assert (await _owners(campaign_id))["chat0"] == "a2"


@pytest.mark.asyncio
async def test_a_receiver_gets_no_more_chats_than_sends_it_has_left(clock: Clock) -> None:
    chats = [chat(f"chat{index}", "a1", "a1") for index in range(4)]
    campaign_id, ctx = await setup(
        ("a1", "a2"), chats, per_hour=3, per_day=50, account_limit=True, join_delay_minutes=0
    )
    for target in chats:
        await _joined(campaign_id, target.key, "a2")
    await _sent(campaign_id, "a2", 2)
    await repository.rest_account(campaign_id, "a1", clock.now() + 3000, "hour_limit")

    # One send left in a2's hour: one chat, the first due; the rest wait for a1.
    assert await _rotation.place_resting(ctx) == 1
    owners = await _owners(campaign_id)
    assert [key for key, owner in owners.items() if owner == "a2"] == ["chat0"]
