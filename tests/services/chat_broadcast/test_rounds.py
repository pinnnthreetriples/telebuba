"""Rounds: rest between them, no join and no pause after one in round 2+, deleted messages."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories import chat_broadcast as repository
from tests.services.chat_broadcast.fakes import run_to_end, seed

if TYPE_CHECKING:
    from tests.services.chat_broadcast.fakes import Clock, FakeTelegram


@pytest.mark.asyncio
async def test_round_two_has_no_join_and_no_pause_after_one(
    telegram: FakeTelegram, clock: Clock
) -> None:
    campaign_id = await seed(
        accounts=("a1",),
        targets=("@alpha",),
        messages=("Hi",),
        loop=True,
        rounds=2,
        join_delay_minutes=60,
        rest_minutes={"min": 60, "max": 60},
    )
    started = clock.now()

    await run_to_end(campaign_id)

    assert telegram.joins == [("a1", "alpha")]
    journal = sorted(await repository.list_journal(campaign_id), key=lambda row: row.id)
    assert [(row.round, row.status) for row in journal] == [(1, "sent"), (2, "sent")]
    first, second = journal
    # Round 1 waited its hour after the join; round 2 went out right after the rest.
    assert clock.now() >= started + 3600 + 3600
    assert first.step_index == second.step_index == 0
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert (record.status, record.round, record.rest_until_unix) == ("done", 2, None)
    [target] = await repository.list_targets(campaign_id)
    assert (target.state, target.round) == ("done", 2)


@pytest.mark.asyncio
async def test_a_chat_whose_message_was_deleted_is_skipped_in_later_rounds(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed(
        accounts=("a1",),
        targets=("@alpha", "@beta"),
        messages=("Hi",),
        loop=True,
        rounds=3,
        rest_minutes={"min": 1, "max": 1},
    )
    # The first message a1 sends is the one an admin will delete.
    telegram.deleted.add(101)

    await run_to_end(campaign_id)

    targets = {t.chat_key: t for t in await repository.list_targets(campaign_id)}
    deleted = [t for t in targets.values() if t.skip_reason == "deleted"]
    assert len(deleted) == 1
    assert deleted[0].message_deleted
    kept = next(t for t in targets.values() if t.skip_reason is None)
    assert (kept.state, kept.round) == ("done", 3)
    statuses = [row.status for row in await repository.list_journal(campaign_id)]
    assert statuses.count("deleted") == 1
    assert len(telegram.sent) == 1 + 3


@pytest.mark.asyncio
async def test_deleted_messages_are_ignored_when_the_switch_is_off(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed(
        accounts=("a1",),
        targets=("@alpha",),
        messages=("Hi",),
        loop=True,
        rounds=2,
        skip_deleted=False,
        rest_minutes={"min": 1, "max": 1},
    )
    telegram.deleted.add(101)

    await run_to_end(campaign_id)

    assert len(telegram.sent) == 2


@pytest.mark.asyncio
async def test_a_chat_skipped_for_good_stays_out_of_the_next_round(
    telegram: FakeTelegram,
) -> None:
    from tests.services.chat_broadcast.fakes import refused  # noqa: PLC0415

    campaign_id = await seed(
        accounts=("a1",),
        targets=("@alpha", "@beta"),
        messages=("Hi",),
        loop=True,
        rounds=2,
        rest_minutes={"min": 1, "max": 1},
    )
    telegram.send_answers["a1"] = [refused(error_type="ChatWriteForbiddenError")]

    await run_to_end(campaign_id)

    assert len(telegram.sent) == 2
    skipped = [t for t in await repository.list_targets(campaign_id) if t.state == "skipped"]
    assert [t.skip_reason for t in skipped] == ["admin_only"]


@pytest.mark.asyncio
async def test_the_time_budget_ends_the_run_during_the_rest(telegram: FakeTelegram) -> None:
    campaign_id = await seed(
        accounts=("a1",),
        targets=("@alpha",),
        messages=("Hi",),
        loop=True,
        rounds=0,
        stop_mode="time",
        stop_hours=1,
        rest_minutes={"min": 120, "max": 120},
    )

    await run_to_end(campaign_id)

    assert len(telegram.sent) == 1
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "done"


@pytest.mark.asyncio
async def test_written_before_by_any_campaign_is_skipped_when_asked(
    telegram: FakeTelegram,
) -> None:
    first = await seed(accounts=("a1",), targets=("@alpha",), messages=("Hi",))
    await run_to_end(first)
    from services import chat_broadcast as service  # noqa: PLC0415

    second = await service.create_campaign("Again")
    current = await service.load_settings(first)
    assert current is not None
    from schemas.chat_broadcast import ChatBroadcastSettingsUpdate  # noqa: PLC0415

    await service.save_settings(
        second.campaign_id,
        ChatBroadcastSettingsUpdate(
            expected_updated_at=second.updated_at,
            name="Again",
            account_ids=["a1"],
            settings=current.settings.model_copy(update={"skip_already_written": True}),
        ),
    )

    await run_to_end(second.campaign_id)

    [target] = await repository.list_targets(second.campaign_id)
    assert target.skip_reason == "already_written"
    assert len(telegram.sent) == 1
