"""Daily limits: a free account's story, the soft start, Telegram's cap, "stop after N"."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories import masstagging as repository
from services.masstagging.pace import day_of, next_day_start
from tests.services.masstagging.fakes import answer, run_to_end, seed

if TYPE_CHECKING:
    from tests.services.masstagging.fakes import Clock, FakeTelegram

_PEOPLE = tuple(f"@person{index}" for index in range(7))


@pytest.mark.asyncio
async def test_a_free_account_rests_until_tomorrow_and_goes_on(
    telegram: FakeTelegram, clock: Clock
) -> None:
    campaign_id = await seed(premium=False, targets=_PEOPLE, mentions_per_story=5)
    tomorrow = next_day_start(clock.now())

    await run_to_end(campaign_id)

    assert [len(story.mentions) for story in telegram.stories] == [5, 2]
    assert clock.now() >= tomorrow
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert (record.run_status, record.rest_until_unix) == ("done", None)


@pytest.mark.asyncio
async def test_the_soft_start_reaches_six_people_a_day(
    telegram: FakeTelegram, clock: Clock
) -> None:
    people = tuple(f"@person{index}" for index in range(8))
    campaign_id = await seed(
        mode="priming", media=[], targets=people, ramp_up=True, dialogs_per_day=40
    )
    first_day = day_of(clock.now())

    await run_to_end(campaign_id)

    days = await repository.fetch_days(["a1"], first_day)
    assert days["a1"].tags == 6
    assert len(telegram.toggles) == 8


@pytest.mark.asyncio
async def test_telegrams_own_story_cap_rests_the_account(
    telegram: FakeTelegram, clock: Clock
) -> None:
    campaign_id = await seed(targets=("@alpha",))
    telegram.story_answers["a1"] = [
        answer("failed", error_type="BadRequestError", error_message="STORIES_TOO_MUCH")
    ]
    tomorrow = next_day_start(clock.now())

    await run_to_end(campaign_id)

    assert telegram.tagged() == ["alpha"]
    assert clock.now() >= tomorrow


@pytest.mark.asyncio
async def test_stop_after_n_pauses_and_continue_counts_afresh(telegram: FakeTelegram) -> None:
    people = ("@a1111", "@b2222", "@c3333")
    campaign_id = await seed(mode="priming", media=[], targets=people, stop_tags=2)

    await run_to_end(campaign_id)

    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert (record.run_status, record.last_error) == ("paused", "volume")
    assert len(telegram.toggles) == 2

    await run_to_end(campaign_id)

    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.run_status == "done"
    assert len(telegram.toggles) == 3


@pytest.mark.asyncio
async def test_stop_after_minutes_counts_from_the_start(telegram: FakeTelegram) -> None:
    people = ("@a1111", "@b2222", "@c3333")
    campaign_id = await seed(
        mode="priming",
        media=[],
        targets=people,
        stop_mode="time",
        stop_minutes=1,
        dialog_gap_seconds={"min": 40, "max": 40},
    )

    await run_to_end(campaign_id)

    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert (record.run_status, record.last_error) == ("paused", "volume")
    assert len(telegram.toggles) == 2


@pytest.mark.asyncio
async def test_a_batch_that_posts_nothing_owes_no_pause(
    telegram: FakeTelegram, clock: Clock
) -> None:
    from schemas.telegram_actions_masstagging import MasstagResolvedTarget  # noqa: PLC0415

    campaign_id = await seed(
        mode="priming",
        media=[],
        targets=("@ghost", "@alpha"),
        dialog_gap_seconds={"min": 600, "max": 600},
    )
    telegram.lookups["ghost"] = MasstagResolvedTarget(outcome="not_found")
    start = clock.now()

    await run_to_end(campaign_id)

    assert len(telegram.toggles) == 1
    # Only the one real toggle is followed by its 600 s pause.
    assert clock.now() - start < 1200
