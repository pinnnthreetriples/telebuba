"""The two modes end to end: a story with mentions (with and without the dialog), priming."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories import masstagging as repository
from schemas.masstagging import MasstaggingSettingsUpdate
from schemas.telegram_actions_masstagging import MasstagUser
from services import masstagging as service
from tests.services.masstagging.fakes import answer, png, run_to_end, seed

if TYPE_CHECKING:
    from tests.services.masstagging.fakes import FakeTelegram


async def _states(campaign_id: str) -> dict[str, str]:
    return {t.raw: t.state for t in await repository.list_targets(campaign_id)}


@pytest.mark.asyncio
async def test_one_story_mentions_the_whole_batch(telegram: FakeTelegram) -> None:
    campaign_id = await seed(targets=("@alpha", "@beta", "@gamma"), mentions_per_story=2)

    await run_to_end(campaign_id)

    assert [story.mentions for story in telegram.stories] == [["alpha", "beta"], ["gamma"]]
    assert telegram.toggles == []
    assert set((await _states(campaign_id)).values()) == {"tagged"}
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.run_status == "done"
    ids = [telegram.user_id(name) for name in ("alpha", "beta", "gamma")]
    assert await repository.tagged_user_ids(ids) == set(ids)


@pytest.mark.asyncio
async def test_the_dialog_is_opened_before_the_story(telegram: FakeTelegram) -> None:
    campaign_id = await seed(targets=("@alpha", "@beta"), prime_first=True)

    await run_to_end(campaign_id)

    alpha, beta = telegram.user_id("alpha"), telegram.user_id("beta")
    assert telegram.toggles == [("a1", str(alpha)), ("a1", str(beta))]
    assert telegram.stories[0].mentions == ["alpha", "beta"]
    events = [
        (entry.target_key, entry.event) for entry in await repository.list_journal(campaign_id)
    ]
    assert ("u:alpha", "dialog_created") in events
    assert ("u:alpha", "tagged") in events


@pytest.mark.asyncio
async def test_priming_only_toggles_the_timer(telegram: FakeTelegram) -> None:
    campaign_id = await seed(mode="priming", media=[], targets=("@alpha", "@beta"))

    await run_to_end(campaign_id)

    assert telegram.stories == []
    assert [peer for _account, peer in telegram.toggles] == [
        str(telegram.user_id("alpha")),
        str(telegram.user_id("beta")),
    ]
    assert set((await _states(campaign_id)).values()) == {"tagged"}


@pytest.mark.asyncio
async def test_the_story_carries_the_campaign_options(telegram: FakeTelegram) -> None:
    campaign_id = await seed(
        caption="Free lesson",
        link="https://t.me/school",
        visibility="contacts",
        lifetime_hours=48,
    )

    await run_to_end(campaign_id)

    story = telegram.stories[0]
    assert story.caption == "Free lesson"
    assert story.link == "https://t.me/school"
    assert story.privacy == "contacts"
    assert story.period == 48 * 3600


@pytest.mark.asyncio
async def test_a_free_account_gets_no_link_and_a_day_long_story(telegram: FakeTelegram) -> None:
    campaign_id = await seed(premium=False, link="https://t.me/school", caption="x" * 500)

    await run_to_end(campaign_id)

    story = telegram.stories[0]
    assert story.link is None
    assert story.period == 24 * 3600
    assert len(story.caption) + len(" @alpha @beta") <= 200


@pytest.mark.asyncio
async def test_a_premium_refusal_posts_again_without_link_and_lifetime(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed(link="https://t.me/school")
    telegram.story_answers["a1"] = [
        answer("failed", error_type="PremiumAccountRequiredError"),
    ]

    await run_to_end(campaign_id)

    assert telegram.stories[0].link is None
    assert telegram.stories[0].period == 24 * 3600
    assert set((await _states(campaign_id)).values()) == {"tagged"}


@pytest.mark.asyncio
async def test_media_is_taken_in_turn(telegram: FakeTelegram) -> None:
    second = await service.upload_media("second.jpg", png())
    campaign_id = await seed(targets=("@alpha", "@beta"), mentions_per_story=1)
    settings = await service.load_settings(campaign_id)
    assert settings is not None
    media = [*settings.settings.media, second]
    await service.save_settings(
        campaign_id,
        MasstaggingSettingsUpdate(
            expected_updated_at=settings.updated_at,
            name="Spring",
            account_ids=settings.account_ids,
            settings=settings.settings.model_copy(update={"media": media}),
        ),
    )
    await run_to_end(campaign_id)

    assert [story.filename for story in telegram.stories] == ["story.png", "story.jpg"]


@pytest.mark.asyncio
async def test_contacts_are_tagged_by_the_account_that_knows_them(telegram: FakeTelegram) -> None:
    campaign_id = await seed(
        accounts=("a1", "a2"), target_source="contacts", targets=(), mode="priming", media=[]
    )
    telegram.contacts["a1"] = [
        MasstagUser(user_id=1, username="alpha"),
        MasstagUser(user_id=2, first_name="Olga"),
    ]
    telegram.contacts["a2"] = [
        MasstagUser(user_id=2, first_name="Olga"),
        MasstagUser(user_id=3, username="robot", is_bot=True),
        MasstagUser(user_id=4, first_name="Petr"),
    ]

    await run_to_end(campaign_id)

    assert sorted(telegram.toggles) == [("a1", "1"), ("a1", "2"), ("a2", "4")]
    assert telegram.resolves == []
    assert set((await _states(campaign_id)).values()) == {"tagged"}
