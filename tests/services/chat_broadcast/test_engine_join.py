"""Joining: each chat gets ONE account, which joins, waits the pause, then writes."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories import chat_broadcast as repository
from tests.services.chat_broadcast.fakes import refused, run_to_end, seed

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult
    from tests.services.chat_broadcast.fakes import Clock, FakeTelegram


@pytest.mark.asyncio
async def test_each_chat_gets_one_account_round_robin(telegram: FakeTelegram) -> None:
    campaign_id = await seed(targets=("@alpha", "@beta", "@gamma"))

    await run_to_end(campaign_id)

    assert sorted(telegram.joins) == [("a1", "alpha"), ("a1", "gamma"), ("a2", "beta")]
    assert sorted((s.account_id, s.text) for s in telegram.sent if s.text == "Second") == [
        ("a1", "Second"),
        ("a1", "Second"),
        ("a2", "Second"),
    ]
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "done"
    targets = await repository.list_targets(campaign_id)
    assert {t.state for t in targets} == {"done"}


@pytest.mark.asyncio
async def test_variants_are_resolved_per_send(telegram: FakeTelegram) -> None:
    campaign_id = await seed(targets=("@alpha",), messages=("{Hi|Hey} there",))

    await run_to_end(campaign_id)

    assert telegram.texts()[0] in {"Hi there", "Hey there"}


@pytest.mark.asyncio
async def test_a_fresh_join_waits_the_pause_and_an_existing_member_writes_at_once(
    telegram: FakeTelegram, clock: Clock
) -> None:
    campaign_id = await seed(
        accounts=("a1",), targets=("@alpha", "@beta"), messages=("Hi",), join_delay_minutes=60
    )
    telegram.join_answers[("a1", "beta")] = [refused("already_participant")]
    started = clock.now()

    await run_to_end(campaign_id)

    events = await repository.list_events(campaign_id)
    joined = {e.chat_key: e for e in events if e.kind in {"joined", "already_member"}}
    assert joined["alpha"].kind == "joined"
    assert int(joined["alpha"].detail or 0) >= started + 3600
    assert joined["beta"].kind == "already_member"
    # Beta was written while alpha still waited out its hour.
    journal = await repository.list_journal(campaign_id)
    first, second = sorted(journal, key=lambda row: row.id)
    assert (first.chat_key, second.chat_key) == ("beta", "alpha")
    assert (second.sent_unix or 0) >= (first.sent_unix or 0)


@pytest.mark.asyncio
async def test_a_join_request_waits_for_approval_then_writes(
    telegram: FakeTelegram, clock: Clock
) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("t.me/+AbCdEfGh123",), messages=("Hi",))
    token = "+AbCdEfGh123"
    telegram.join_answers[("a1", token)] = [refused(error_type="InviteRequestSentError")]
    telegram.hidden.add(token)
    telegram.approve_at[token] = clock.now() + 3 * 3600

    await run_to_end(campaign_id)

    kinds = [e.kind for e in await repository.list_events(campaign_id)]
    assert kinds[:3] == ["requested", "approved", "joined"]
    assert telegram.texts() == ["Hi"]


@pytest.mark.asyncio
async def test_a_request_not_approved_in_time_is_skipped(telegram: FakeTelegram) -> None:
    campaign_id = await seed(
        accounts=("a1",), targets=("t.me/+AbCdEfGh123",), approval_wait_hours=1
    )
    telegram.join_answers[("a1", "+AbCdEfGh123")] = [refused(error_type="InviteRequestSentError")]
    telegram.hidden.add("+AbCdEfGh123")

    await run_to_end(campaign_id)

    [target] = await repository.list_targets(campaign_id)
    assert (target.state, target.skip_reason) == ("skipped", "not_approved")
    assert telegram.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (refused(error_type="InviteHashExpiredError"), "invalid_link"),
        (refused(error_type="ChannelPrivateError"), "banned"),
        (refused(error_message="chat_not_found"), "invalid_link"),
    ],
)
async def test_a_refused_join_skips_the_chat(
    telegram: FakeTelegram, answer: ActionResult, reason: str
) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@alpha",))
    telegram.join_answers[("a1", "alpha")] = [answer]

    await run_to_end(campaign_id)

    [target] = await repository.list_targets(campaign_id)
    assert (target.state, target.skip_reason) == ("skipped", reason)


@pytest.mark.asyncio
async def test_a_broadcast_channel_is_admin_only(telegram: FakeTelegram) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@news",))
    telegram.kinds["news"] = "channel"

    await run_to_end(campaign_id)

    [target] = await repository.list_targets(campaign_id)
    assert target.skip_reason == "admin_only"


@pytest.mark.asyncio
async def test_a_failed_captcha_skips_the_chat(telegram: FakeTelegram) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@alpha",))
    telegram.captcha = "give_up"

    await run_to_end(campaign_id)

    [target] = await repository.list_targets(campaign_id)
    assert target.skip_reason == "captcha"
    assert telegram.sent == []


@pytest.mark.asyncio
async def test_a_flooded_join_hands_the_chat_to_another_account(telegram: FakeTelegram) -> None:
    campaign_id = await seed(accounts=("a1", "a2"), targets=("@alpha",), messages=("Hi",))
    telegram.join_answers[("a1", "alpha")] = [refused("flood_wait", flood_wait_seconds=500)]

    await run_to_end(campaign_id)

    assert ("a2", "alpha") in telegram.joins
    assert [s.account_id for s in telegram.sent] == ["a2"]
    roster = {r.account_id: r for r in await repository.list_roster(campaign_id)}
    assert (roster["a1"].state, roster["a1"].halted_reason) == ("halted", "flood_wait")
    [target] = await repository.list_targets(campaign_id)
    assert target.handed_from_account_id == "a1"
