"""``account_story_media`` — full story media for the profile modal's player."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.telegram_client import TelegramReadError
from schemas.accounts import AccountProfileSnapshot
from schemas.telegram_actions import DownloadStoryMedia
from schemas.telegram_profile_snapshot import TelegramStoryMedia, TelegramStoryThumb
from services.accounts.profile_read import account_story_media

if TYPE_CHECKING:
    from pydantic import BaseModel


@pytest.fixture(autouse=True)
def _snapshot_patched(monkeypatch: pytest.MonkeyPatch) -> None:
    async def snapshot(
        account_id: str,  # noqa: ARG001
        *,
        force_refresh: bool = False,  # noqa: ARG001
    ) -> AccountProfileSnapshot:
        return AccountProfileSnapshot(
            account_id="acc-1",
            stories=[TelegramStoryThumb(story_id=9, kind="video", privacy_preset="contacts")],
        )

    monkeypatch.setattr("services.accounts.profile_read.fetch_live_account_profile", snapshot)


@pytest.mark.asyncio
async def test_story_media_downloads_known_story(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[BaseModel] = []

    async def read_many(account_id: str, actions: list[BaseModel]) -> list[BaseModel]:
        assert account_id == "acc-1"
        seen.extend(actions)
        return [TelegramStoryMedia(content=b"mp4", mime_type="video/mp4")]

    monkeypatch.setattr("services.accounts.profile_read.execute_read_many", read_many)

    media = await account_story_media("acc-1", 9)

    assert media is not None
    assert media.content == b"mp4"
    assert media.media_type == "video/mp4"
    assert media.etag
    assert seen == [DownloadStoryMedia(story_id=9)]


@pytest.mark.asyncio
async def test_story_media_unknown_id_never_reaches_telegram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[BaseModel]] = []

    async def read_many(account_id: str, actions: list[BaseModel]) -> list[BaseModel]:  # noqa: ARG001
        calls.append(actions)
        return [TelegramStoryMedia(content=b"never")]

    monkeypatch.setattr("services.accounts.profile_read.execute_read_many", read_many)

    assert await account_story_media("acc-1", 404) is None
    assert calls == [], "an id outside the snapshot must not be downloaded"


@pytest.mark.asyncio
async def test_story_media_read_error_degrades_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    async def read_many(account_id: str, actions: list[BaseModel]) -> list[BaseModel]:  # noqa: ARG001
        reason = "flood_wait"
        raise TelegramReadError(reason)

    monkeypatch.setattr("services.accounts.profile_read.execute_read_many", read_many)

    assert await account_story_media("acc-1", 9) is None


@pytest.mark.asyncio
async def test_story_media_empty_download_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    async def read_many(account_id: str, actions: list[BaseModel]) -> list[BaseModel]:  # noqa: ARG001
        return [TelegramStoryMedia()]

    monkeypatch.setattr("services.accounts.profile_read.execute_read_many", read_many)

    assert await account_story_media("acc-1", 9) is None
