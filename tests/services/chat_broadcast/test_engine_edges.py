"""The engine's rarer paths: the join budget, retried joins, lost membership, time limits."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.repositories import chat_broadcast as repository
from core.repositories.neurocomment import record_join
from schemas.telegram_actions_broadcast import WritableGroupsResult
from tests.services.chat_broadcast.fakes import group, refused, run_to_end, seed

if TYPE_CHECKING:
    from tests.services.chat_broadcast.fakes import Clock, FakeTelegram


@pytest.mark.asyncio
async def test_the_shared_join_budget_defers_joins(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.chat_broadcast, "max_joins_per_account_per_day", 1)
    campaign_id = await seed(accounts=("a1",), targets=("@alpha",), messages=("Hi",), stop_hours=1)
    await record_join("a1")
    from schemas.chat_broadcast import ChatBroadcastSettingsUpdate  # noqa: PLC0415
    from services import chat_broadcast as service  # noqa: PLC0415

    current = await service.load_settings(campaign_id)
    assert current is not None
    await service.save_settings(
        campaign_id,
        ChatBroadcastSettingsUpdate(
            expected_updated_at=current.updated_at,
            name="Crypto",
            account_ids=["a1"],
            settings=current.settings.model_copy(update={"stop_mode": "time"}),
        ),
    )

    await run_to_end(campaign_id)

    assert telegram.joins == []
    [target] = await repository.list_targets(campaign_id)
    assert target.state == "queued"


@pytest.mark.asyncio
async def test_a_join_that_did_not_get_through_is_retried(telegram: FakeTelegram) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@alpha",), messages=("Hi",))
    telegram.join_answers[("a1", "alpha")] = [
        refused("unavailable", error_type="ConnectionError"),
        refused("ok"),
    ]

    await run_to_end(campaign_id)

    assert telegram.joins == [("a1", "alpha"), ("a1", "alpha")]
    assert telegram.texts() == ["Hi"]


@pytest.mark.asyncio
async def test_an_unexplained_join_failure_counts_as_an_error(telegram: FakeTelegram) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@alpha",), messages=("Hi",))
    telegram.join_answers[("a1", "alpha")] = [refused(error_type="RPCError")]

    await run_to_end(campaign_id)

    [target] = await repository.list_targets(campaign_id)
    assert target.skip_reason == "error"
    [account] = await repository.list_roster(campaign_id)
    assert account.consecutive_errors == 1


@pytest.mark.asyncio
async def test_a_chat_that_forgets_us_after_a_join_is_unreachable(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@alpha",), messages=("Hi",))
    telegram.send_answers["a1"] = [refused(error_type="UserNotParticipantError")]

    await run_to_end(campaign_id)

    [target] = await repository.list_targets(campaign_id)
    assert target.skip_reason == "unreachable"


@pytest.mark.asyncio
async def test_an_own_group_we_were_removed_from_is_joined_again_by_username(
    telegram: FakeTelegram,
) -> None:
    telegram.groups["a1"] = WritableGroupsResult(groups=[group(9, "Chat", "chat_nine")])
    campaign_id = await seed(accounts=("a1",), target_mode="own", targets=(), messages=("Hi",))
    telegram.send_answers["a1"] = [refused(error_type="UserNotParticipantError")]

    await run_to_end(campaign_id)

    assert telegram.joins == [("a1", "chat_nine")]
    assert telegram.texts() == ["Hi"]


@pytest.mark.asyncio
async def test_an_own_group_without_a_username_cannot_be_rejoined(
    telegram: FakeTelegram,
) -> None:
    telegram.groups["a1"] = WritableGroupsResult(groups=[group(9, "Private")])
    campaign_id = await seed(accounts=("a1",), target_mode="own", targets=(), messages=("Hi",))
    telegram.send_answers["a1"] = [refused(error_type="UserNotParticipantError")]

    await run_to_end(campaign_id)

    [target] = await repository.list_targets(campaign_id)
    assert target.skip_reason == "unreachable"


@pytest.mark.asyncio
async def test_the_time_limit_stops_the_chain(telegram: FakeTelegram, clock: Clock) -> None:
    campaign_id = await seed(
        accounts=("a1",),
        targets=("@alpha", "@beta"),
        messages=("Hi",),
        stop_mode="time",
        stop_hours=1,
        between_chats={"min": 1800, "max": 1800},
    )

    await run_to_end(campaign_id)

    assert telegram.texts() == ["Hi"]
    assert clock.now() > 0


@pytest.mark.asyncio
async def test_an_empty_text_step_is_passed_over(telegram: FakeTelegram) -> None:
    campaign_id = await seed(
        accounts=("a1",),
        targets=("@alpha",),
        messages=("{|}", "Second"),
    )

    await run_to_end(campaign_id)

    assert telegram.texts() == ["Second"]
    statuses = [(row.step_index, row.status) for row in await repository.list_journal(campaign_id)]
    assert statuses == [(0, "failed"), (1, "sent")]
