"""Campaign policy: draft/ready from the settings, the dialog's save, delete, the gear."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.repositories.spam_status import upsert_spam_status
from schemas.masstagging import (
    MasstaggingPace,
    MasstaggingSettings,
    MasstaggingSettingsUpdate,
)
from schemas.spam_status import SpamStatusVerdict
from services import masstagging as service
from services.masstagging import _state
from tests.services.masstagging.fakes import account, png, seed

if TYPE_CHECKING:
    from tests.services.masstagging.fakes import FakeTelegram


async def _save(campaign_id: str, **values: object) -> None:
    read = await service.load_settings(campaign_id)
    assert read is not None
    await service.save_settings(
        campaign_id,
        MasstaggingSettingsUpdate(
            expected_updated_at=read.updated_at,
            name=read.name,
            account_ids=list(values.pop("account_ids", read.account_ids)),  # ty: ignore[invalid-argument-type]
            settings=read.settings.model_copy(update=values),
        ),
    )


async def _campaign(campaign_id: str) -> tuple[str, list[str]]:
    items = (await service.list_campaigns()).items
    found = next(item for item in items if item.campaign_id == campaign_id)
    return found.status, list(found.missing)


@pytest.mark.asyncio
async def test_a_new_campaign_is_a_draft_listing_what_it_lacks() -> None:
    created = await service.create_campaign("  Spring  ")

    assert created.name == "Spring"
    assert await _campaign(created.campaign_id) == (
        "draft",
        ["no_accounts", "no_targets", "no_media"],
    )


@pytest.mark.asyncio
async def test_a_complete_campaign_is_ready() -> None:
    campaign_id = await seed()

    assert await _campaign(campaign_id) == ("ready", [])


@pytest.mark.asyncio
async def test_priming_needs_no_media_and_contacts_need_no_list() -> None:
    campaign_id = await seed(mode="priming", media=[], target_source="contacts", targets=())

    assert await _campaign(campaign_id) == ("ready", [])


@pytest.mark.asyncio
async def test_an_all_spam_blocked_roster_is_named() -> None:
    campaign_id = await seed()
    await upsert_spam_status(
        SpamStatusVerdict(account_id="a1", status="limited", checked_at="2026-10-11T00:00:00")
    )

    assert await _campaign(campaign_id) == ("draft", ["all_spamblocked"])


@pytest.mark.asyncio
async def test_the_save_normalises_the_list_and_refuses_bad_input() -> None:
    campaign_id = await seed()
    await _save(campaign_id, targets=["@Ivan", "ivan", "t.me/olga"])
    read = await service.load_settings(campaign_id)
    assert read is not None
    assert read.settings.targets == ["@Ivan", "@olga"]

    with pytest.raises(service.MasstaggingInvalidError, match="masstagging_invalid_target"):
        await _save(campaign_id, targets=["t.me/+invite"])
    with pytest.raises(service.MasstaggingInvalidError, match="account_not_found"):
        await _save(campaign_id, account_ids=["ghost"])


@pytest.mark.asyncio
async def test_too_many_media_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config import settings  # noqa: PLC0415

    campaign_id = await seed()
    media = await service.upload_media("b.jpg", png())
    read = await service.load_settings(campaign_id)
    assert read is not None
    monkeypatch.setattr(settings.masstagging, "max_media_per_campaign", 1)

    with pytest.raises(service.MasstaggingInvalidError, match="masstagging_too_many_media"):
        await _save(campaign_id, media=[*read.settings.media, media])


@pytest.mark.asyncio
async def test_a_stale_save_is_refused() -> None:
    campaign_id = await seed()
    read = await service.load_settings(campaign_id)
    assert read is not None
    await _save(campaign_id, caption="new")

    with pytest.raises(service.MasstaggingConflictError, match="campaign_changed"):
        await service.save_settings(
            campaign_id,
            MasstaggingSettingsUpdate(
                expected_updated_at=read.updated_at,
                name="x",
                account_ids=[],
                settings=MasstaggingSettings(),
            ),
        )


@pytest.mark.asyncio
async def test_a_running_campaign_keeps_its_settings_but_takes_the_pace(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed()
    telegram.hold = asyncio.Event()
    assert await service.start_campaign(campaign_id)

    with pytest.raises(service.MasstaggingConflictError, match="campaign_running"):
        await _save(campaign_id, caption="new")
    with pytest.raises(service.MasstaggingConflictError, match="campaign_running"):
        await service.delete_campaign(campaign_id)
    saved = await service.save_pace(campaign_id, MasstaggingPace(stories_per_day=3))

    assert saved is not None
    assert saved.settings.stories_per_day == 3
    # The seed's pauses survive a body that names only the stories a day.
    assert saved.settings.story_gap_minutes.min == 0
    live = _state.live_pace(campaign_id)
    assert live is not None
    assert (live.stories_per_day, live.story_gap_minutes.max) == (3, 0)
    await service.stop_campaign(campaign_id)


@pytest.mark.asyncio
async def test_delete_and_unknown_campaigns() -> None:
    campaign_id = await seed()

    assert await service.delete_campaign(campaign_id) is True
    assert await service.delete_campaign(campaign_id) is None
    assert await service.load_settings(campaign_id) is None
    assert await service.save_pace(campaign_id, MasstaggingPace()) is None
    assert await service.campaign_exists(campaign_id) is False


@pytest.mark.asyncio
async def test_story_media_must_be_a_photo_or_a_video() -> None:
    video = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32

    stored = await service.upload_media("clip.MP4", video)

    assert stored.media_id.endswith(".mp4")
    with pytest.raises(service.MasstaggingInvalidError, match="masstagging_media_invalid"):
        await service.upload_media("notes.txt", b"hello")
    with pytest.raises(service.MasstaggingInvalidError, match="masstagging_media_invalid"):
        await service.upload_media("fake.mp4", b"not a video at all")


@pytest.mark.asyncio
async def test_a_too_large_file_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.config import settings  # noqa: PLC0415

    monkeypatch.setattr(settings.profile_media, "story_image_max_bytes", 10)

    with pytest.raises(service.MasstaggingInvalidError, match="masstagging_media_too_large"):
        await service.upload_media("a.png", png())


@pytest.mark.asyncio
async def test_list_counts_roster_and_planned_list() -> None:
    campaign_id = await seed(accounts=("a1", "a2"), targets=("@alpha", "@beta", "@gamma"))
    await account("loose")

    item = next(c for c in (await service.list_campaigns()).items if c.campaign_id == campaign_id)

    assert (item.account_count, item.target_count, item.mode) == (2, 3, "stories")


@pytest.mark.asyncio
async def test_a_dialog_save_replaces_the_gear_s_pace() -> None:
    campaign_id = await seed()
    await service.save_pace(campaign_id, MasstaggingPace(stories_per_day=3))

    await _save(campaign_id, stories_per_day=7)

    assert _state.live_pace(campaign_id) is None
    board = await service.load_board(campaign_id)
    assert board is not None
    assert board.counters.per_day == 35
