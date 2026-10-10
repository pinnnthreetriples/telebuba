"""An account already inside a chat neither joins it again nor waits the post-join pause.

Telegram answers a re-join of a public chat with a plain success, so the join itself
cannot tell: a repeated run keeps the membership its last run learnt, and a fresh one
asks before joining.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories import chat_broadcast as repository
from tests.services.chat_broadcast.fakes import refused, run_to_end, seed

if TYPE_CHECKING:
    from tests.services.chat_broadcast.fakes import Clock, FakeTelegram


@pytest.mark.asyncio
async def test_a_repeated_run_writes_at_once_where_its_accounts_already_are(
    telegram: FakeTelegram, clock: Clock
) -> None:
    campaign_id = await seed(
        targets=("@alpha", "@beta", "@gamma"), messages=("Hi",), join_delay_minutes=60
    )
    await run_to_end(campaign_id)
    first_joins = sorted(telegram.joins)
    telegram.joins.clear()
    started = clock.now()

    await run_to_end(campaign_id)

    assert first_joins == [("a1", "alpha"), ("a1", "gamma"), ("a2", "beta")]
    assert telegram.joins == []
    targets = await repository.list_targets(campaign_id)
    assert {(t.chat_key, t.assigned_account_id, t.state) for t in targets} == {
        ("alpha", "a1", "done"),
        ("beta", "a2", "done"),
        ("gamma", "a1", "done"),
    }
    # Nothing waited out the hour: the run is over well before it.
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "done"
    assert clock.now() - started < 3600


@pytest.mark.asyncio
async def test_a_repeated_run_keeps_a_chat_with_the_account_inside_it(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed(accounts=("a1", "a2"), targets=("@alpha",), messages=("Hi",))
    await run_to_end(campaign_id)
    [first] = await repository.list_targets(campaign_id)
    telegram.joins.clear()

    await run_to_end(campaign_id)

    [again] = await repository.list_targets(campaign_id)
    assert again.member_account_id == first.member_account_id == "a1"
    assert telegram.joins == []


@pytest.mark.asyncio
async def test_a_new_campaign_does_not_join_where_the_account_already_is(
    telegram: FakeTelegram, clock: Clock
) -> None:
    campaign_id = await seed(
        accounts=("a1",), targets=("@alpha",), messages=("Hi",), join_delay_minutes=60
    )
    telegram.members.add(("a1", "alpha"))
    started = clock.now()

    await run_to_end(campaign_id)

    assert telegram.joins == []
    assert telegram.texts() == ["Hi"]
    events = await repository.list_events(campaign_id)
    [inside] = [e for e in events if e.kind in {"joined", "already_member"}]
    assert inside.kind == "already_member"
    assert clock.now() - started < 3600


@pytest.mark.asyncio
async def test_a_membership_gone_stale_since_the_last_run_joins_again(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@alpha",), messages=("Hi",))
    await run_to_end(campaign_id)
    telegram.joins.clear()
    # Kicked between runs: the first send of the new run is refused.
    telegram.send_answers["a1"] = [refused(error_type="UserNotParticipantError")]

    await run_to_end(campaign_id)

    assert telegram.joins == [("a1", "alpha")]
    assert telegram.texts() == ["Hi", "Hi"]
