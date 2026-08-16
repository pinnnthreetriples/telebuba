"""The observed-chat log: idempotent polling, the cursor, and the reply claim."""

from __future__ import annotations

import pytest

from core.repositories.neuroshilling import (
    chat_cursor,
    claim_chat_reply,
    count_chat_activity,
    count_chat_reply_usage,
    create_campaign,
    list_recent_chat,
    record_chat_messages,
    record_chat_reply,
)
from schemas.neuroshilling import NeuroshillingCampaignCreate, NeuroshillingChatMessage

_TARGET = "@alpha"
_PAST = "1970-01-01T00:00:00+00:00"
_FUTURE = "2999-01-01T00:00:00+00:00"


def _message(
    message_id: int, text: str = "hi", *, is_ours: bool = False
) -> NeuroshillingChatMessage:
    return NeuroshillingChatMessage(message_id=message_id, text=text, is_ours=is_ours)


async def _campaign() -> str:
    created = await create_campaign(NeuroshillingCampaignCreate(name="Promo"))
    return created.campaign_id


@pytest.mark.asyncio
async def test_a_re_poll_of_the_same_page_records_nothing_twice() -> None:
    """Overlapping polls are the normal case, so "new" has to mean new.

    Everything downstream — the reply decision above all — is driven by the RETURN
    value, so a second poll that reported the same messages as fresh would offer
    them for an answer again and pay for a second model call on each.
    """
    campaign_id = await _campaign()
    first = await record_chat_messages(campaign_id, _TARGET, [_message(7), _message(8)])
    second = await record_chat_messages(campaign_id, _TARGET, [_message(8), _message(9)])

    assert [item.message_id for item in first] == [7, 8]
    assert [item.message_id for item in second] == [9]


@pytest.mark.asyncio
async def test_the_cursor_is_the_highest_id_of_the_pair_and_starts_at_zero() -> None:
    """Zero is a real cursor, not a missing one: the gateway reads it as "latest page"."""
    campaign_id = await _campaign()

    assert await chat_cursor(campaign_id, _TARGET) == 0

    await record_chat_messages(campaign_id, _TARGET, [_message(7), _message(30), _message(8)])

    assert await chat_cursor(campaign_id, _TARGET) == 30
    # Scoped to the pair: another target of the same campaign has its own cursor.
    assert await chat_cursor(campaign_id, "@beta") == 0


@pytest.mark.asyncio
async def test_the_context_read_is_the_newest_page_in_the_order_it_was_said() -> None:
    campaign_id = await _campaign()
    await record_chat_messages(
        campaign_id,
        _TARGET,
        [_message(index, f"line {index}") for index in (1, 2, 3, 4)],
    )

    recent = await list_recent_chat(campaign_id, _TARGET, limit=2)

    assert [item.message_id for item in recent] == [3, 4]


@pytest.mark.asyncio
async def test_only_one_caller_can_take_the_right_to_answer_a_message() -> None:
    """The claim is never given back, whatever becomes of the answer.

    A refused or failed reply that released its claim would be retried, and the
    retry would spend another model call on the same attacker-supplied text — with
    a second chance of publishing what the first attempt caught.
    """
    campaign_id = await _campaign()
    await record_chat_messages(campaign_id, _TARGET, [_message(7)])

    assert await claim_chat_reply(campaign_id, _TARGET, 7) is True
    assert await claim_chat_reply(campaign_id, _TARGET, 7) is False


@pytest.mark.asyncio
async def test_two_campaigns_aimed_at_one_chat_do_not_both_answer_the_same_message() -> None:
    """The rows are per campaign; the CHAT is not, and the stranger reading it is not.

    Scoped to the campaign, the claim let one message be answered once per campaign
    pointed at that target — two of our accounts replying to one person within a
    minute from two different fleets, which is the tell the whole engine avoids.
    The claim is taken in either order and it does not matter which poll runs first.
    """
    first, second = await _campaign(), await _campaign()
    await record_chat_messages(first, _TARGET, [_message(7)])
    await record_chat_messages(second, _TARGET, [_message(7)])

    assert await claim_chat_reply(first, _TARGET, 7) is True
    assert await claim_chat_reply(second, _TARGET, 7) is False


@pytest.mark.asyncio
async def test_a_campaign_that_polls_later_still_finds_the_message_taken() -> None:
    """The rows do not have to exist yet when the first campaign claims.

    Two pollers run on their own clocks, so the second campaign's row is routinely
    inserted after the first has already answered — and an ``= 0`` predicate on a row
    that did not exist to be flipped would have let it through.
    """
    first, second = await _campaign(), await _campaign()
    await record_chat_messages(first, _TARGET, [_message(7)])

    assert await claim_chat_reply(first, _TARGET, 7) is True

    await record_chat_messages(second, _TARGET, [_message(7)])

    assert await claim_chat_reply(second, _TARGET, 7) is False


@pytest.mark.asyncio
async def test_a_message_in_another_chat_is_claimed_independently() -> None:
    """The key is ``(target, message_id)``: ids are only unique within one chat."""
    campaign_id = await _campaign()
    await record_chat_messages(campaign_id, _TARGET, [_message(7)])
    await record_chat_messages(campaign_id, "@beta", [_message(7)])

    assert await claim_chat_reply(campaign_id, _TARGET, 7) is True
    assert await claim_chat_reply(campaign_id, "@beta", 7) is True


@pytest.mark.asyncio
async def test_claiming_a_message_we_never_saw_is_refused() -> None:
    campaign_id = await _campaign()

    assert await claim_chat_reply(campaign_id, _TARGET, 7) is False


@pytest.mark.asyncio
async def test_the_counters_separate_a_decision_from_a_publication() -> None:
    """``replied`` is the decision; only ``replied_at`` means an answer went out."""
    campaign_id = await _campaign()
    await record_chat_messages(campaign_id, _TARGET, [_message(7), _message(8)])
    await claim_chat_reply(campaign_id, _TARGET, 7)
    await claim_chat_reply(campaign_id, _TARGET, 8)
    await record_chat_reply(campaign_id, _TARGET, 8, account_id="acc-1")

    activity = await count_chat_activity(campaign_id)

    assert (activity.seen, activity.replied) == (2, 1)


@pytest.mark.asyncio
async def test_published_replies_are_counted_against_the_account_and_the_chat() -> None:
    """The quota this feeds is the operator's "messages per hour" for the ACCOUNT.

    An autoreply has no journal row, so without this count the same account could
    answer strangers all day under a ceiling that only ever saw its scenario steps.
    """
    campaign_id = await _campaign()
    await record_chat_messages(campaign_id, _TARGET, [_message(7), _message(8)])
    await record_chat_reply(campaign_id, _TARGET, 7, account_id="acc-1")
    await record_chat_reply(campaign_id, _TARGET, 8, account_id="acc-2")

    mine = await count_chat_reply_usage("acc-1", _TARGET, hour_since=_PAST, day_since=_PAST)
    elsewhere = await count_chat_reply_usage("acc-1", "@beta", hour_since=_PAST, day_since=_PAST)
    stale = await count_chat_reply_usage("acc-1", _TARGET, hour_since=_FUTURE, day_since=_FUTURE)

    assert (mine.hour, mine.chat_day) == (1, 1)
    assert (elsewhere.hour, elsewhere.chat_day) == (1, 0)
    assert (stale.hour, stale.chat_day) == (0, 0)
