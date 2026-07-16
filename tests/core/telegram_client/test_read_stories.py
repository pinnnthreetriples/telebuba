"""Story read tests for the Telegram gateway."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.functions.stories import GetPinnedStoriesRequest
from telethon.tl.types import (
    MessageMediaDocument,
    MessageMediaPhoto,
)

from core.config import settings
from core.telegram_client import (
    execute_read,
)
from schemas.telegram_actions import (
    ListActiveStories,
    ListPinnedStories,
)
from schemas.telegram_profile_snapshot import (
    TelegramActiveStories,
    TelegramPinnedStories,
)
from tests.core.telegram_client.helpers import patch_read_client as _patch_client


@pytest.mark.asyncio
async def test_list_pinned_stories_returns_items(monkeypatch: pytest.MonkeyPatch) -> None:
    photo_media = MagicMock(spec=MessageMediaPhoto)
    video_doc = MagicMock(mime_type="video/mp4")
    video_media = MagicMock(spec=MessageMediaDocument)
    video_media.document = video_doc
    self_peer = MagicMock(name="InputPeerSelf")

    class FakeStory:
        def __init__(self, story_id: int, media: object, caption: str | None) -> None:
            self.id = story_id
            self.media = media
            self.caption = caption

    stories_payload = MagicMock(
        stories=[
            FakeStory(101, photo_media, "первая"),
            FakeStory(102, video_media, None),
        ],
    )
    requested: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, name: str) -> object:
            assert name == "me"
            return self_peer

        async def __call__(self, request: object) -> object:
            requested.append(request)
            return stories_payload

        async def download_media(self, _media: object, *, file: object, thumb: int) -> bytes:  # noqa: ARG002
            # Carousel needs the largest cached preview, not the stripped thumb.
            assert thumb == -1
            return b"thumb"

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-stories", ListPinnedStories(limit=5))

    assert isinstance(result, TelegramPinnedStories)
    assert [item.story_id for item in result.items] == [101, 102]
    assert result.items[0].kind == "image"
    assert result.items[0].caption == "первая"
    assert result.items[0].thumb_bytes == b"thumb"
    assert result.items[1].kind == "video"
    assert any(isinstance(req, GetPinnedStoriesRequest) for req in requested)


@pytest.mark.asyncio
async def test_list_pinned_stories_captures_view_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """``StoryItem.views`` view + reaction counts must land on the snapshot row.

    A story that omits view data (Telegram returns no ``views`` object for
    expired, unpinned stories) maps to ``None``, not a crash.
    """
    with_views = MagicMock(
        id=401,
        media=MagicMock(spec=MessageMediaPhoto),
        caption=None,
        views=MagicMock(views_count=137, reactions_count=12),
    )
    without_views = MagicMock(
        id=402,
        media=MagicMock(spec=MessageMediaPhoto),
        caption=None,
        views=None,
    )
    stories_payload = MagicMock(stories=[with_views, without_views])

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, name: str) -> object:  # noqa: ARG002
            return MagicMock()

        async def __call__(self, request: object) -> object:  # noqa: ARG002
            return stories_payload

        async def download_media(self, _media: object, *, file: object, thumb: int) -> bytes:  # noqa: ARG002
            return b"thumb"

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-story-views", ListPinnedStories(limit=5))

    assert isinstance(result, TelegramPinnedStories)
    assert result.items[0].views == 137
    assert result.items[0].reactions == 12
    assert result.items[1].views is None
    assert result.items[1].reactions is None


@pytest.mark.asyncio
async def test_list_active_stories_extracts_inner_stories_and_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``stories.getPeerStories`` returns a doubly-nested ``stories`` chain.

    The outer ``stories`` attribute is a ``PeerStories`` constructor — the
    actual ``StoryItem`` list lives one level deeper at
    ``result.stories.stories``. Regression guard for the same trap the
    research subagent flagged: many third-party stubs assume the flat
    layout. Also asserts that the ``StoryItem.pinned`` and privacy-preset
    flags are carried through to the snapshot row so the UI badges can
    render without a second round-trip.
    """
    from telethon.tl.functions.stories import GetPeerStoriesRequest  # noqa: PLC0415

    pinned_story = MagicMock(
        id=301,
        media=MagicMock(spec=MessageMediaPhoto),
        caption="закреп",
        pinned=True,
        public=True,
        close_friends=False,
        contacts=False,
        selected_contacts=False,
        date=datetime(2026, 6, 19, 10, 0, tzinfo=UTC),
        expire_date=datetime(2099, 1, 1, tzinfo=UTC),
    )
    fresh_story = MagicMock(
        id=302,
        media=MagicMock(spec=MessageMediaPhoto),
        caption=None,
        pinned=False,
        public=False,
        close_friends=True,
        contacts=False,
        selected_contacts=False,
        date=datetime(2026, 6, 20, 9, 0, tzinfo=UTC),
        expire_date=datetime(2099, 1, 1, tzinfo=UTC),
    )
    inner_peer_stories = MagicMock(stories=[pinned_story, fresh_story])
    outer_payload = MagicMock(stories=inner_peer_stories)
    requested: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            requested.append(request)
            return outer_payload

        async def download_media(self, _media: object, *, file: object, thumb: int) -> bytes:  # noqa: ARG002
            return b"thumb"

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-active", ListActiveStories())

    assert isinstance(result, TelegramActiveStories)
    assert any(isinstance(req, GetPeerStoriesRequest) for req in requested)
    assert [item.story_id for item in result.items] == [301, 302]
    assert result.items[0].is_pinned is True
    assert result.items[1].is_pinned is False
    # Both are active (expire_date in 2099).
    assert all(item.is_active for item in result.items)
    assert result.items[0].date_unix == int(
        datetime(2026, 6, 19, 10, 0, tzinfo=UTC).timestamp(),
    )
    # Privacy presets are propagated from the StoryItem flag bits — public
    # wins over the others when set; close_friends maps cleanly when alone.
    assert result.items[0].privacy_preset == "public"
    assert result.items[1].privacy_preset == "close_friends"


@pytest.mark.asyncio
async def test_list_pinned_stories_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, _name: str) -> object:
            return MagicMock()

        async def __call__(self, _request: object) -> object:
            return MagicMock(stories=[])

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-empty", ListPinnedStories())

    assert isinstance(result, TelegramPinnedStories)
    assert result.items == []


@pytest.mark.asyncio
async def test_download_story_thumb_swallows_rpc_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    photo_media = MagicMock(spec=MessageMediaPhoto)

    class FakeStory:
        def __init__(self) -> None:
            self.id = 99
            self.media = photo_media
            self.caption = None

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, _name: str) -> object:
            return MagicMock()

        async def __call__(self, _request: object) -> object:
            return MagicMock(stories=[FakeStory()])

        async def download_media(self, _media: object, *, file: object, thumb: int) -> bytes:  # noqa: ARG002
            raise errors.RPCError(request=None, message="MEDIA_INVALID", code=400)

    _patch_client(monkeypatch, FakeClient())

    # A thumb-download RPCError is swallowed inside ``_download_story_thumb``
    # so the story is still listed (with ``thumb_bytes=None``). The outer
    # FloodWait/RPC wrapper triggers only when the *action* fails — keeping
    # one bad thumb from blowing up the whole list view.
    result = await execute_read("acc-stories", ListPinnedStories())

    assert isinstance(result, TelegramPinnedStories)
    assert len(result.items) == 1
    assert result.items[0].story_id == 99
    assert result.items[0].thumb_bytes is None


@pytest.mark.asyncio
async def test_list_pinned_stories_flood_wait_breaks_thumb_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first FloodWait stops the remaining story thumbnail downloads."""
    monkeypatch.setattr(settings.profile_media, "thumb_concurrency", 1)
    photo_media = MagicMock(spec=MessageMediaPhoto)

    class FakeStory:
        def __init__(self, story_id: int) -> None:
            self.id = story_id
            self.media = photo_media
            self.caption = None

    attempts: list[int] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            return MagicMock(stories=[FakeStory(1), FakeStory(2), FakeStory(3)])

        async def download_media(self, _media: object, *, file: object, thumb: int) -> bytes:  # noqa: ARG002
            attempts.append(1)
            raise errors.FloodWaitError(request=None, capture=27)

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-story-flood", ListPinnedStories())

    assert isinstance(result, TelegramPinnedStories)
    assert [item.story_id for item in result.items] == [1, 2, 3]
    assert all(item.thumb_bytes is None for item in result.items)
    assert len(attempts) == 1, "siblings must skip after the breaker trips"
