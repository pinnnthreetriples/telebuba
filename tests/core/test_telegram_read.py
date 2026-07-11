"""Tests for ``core.telegram_client.execute_read`` — read-action dispatcher."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.functions.photos import GetUserPhotosRequest
from telethon.tl.functions.stories import GetPinnedStoriesRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import (
    DocumentAttributeAudio,
    MessageMediaDocument,
    MessageMediaPhoto,
)

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.telegram_client import (
    TelegramAccountNotFoundError,
    TelegramReadError,
    execute_read,
    execute_read_many,
)
from schemas.telegram_actions import (
    CheckMessagesAlive,
    CheckMessagesAliveResult,
    GetLinkedDiscussionGroup,
    GetUserProfile,
    LinkedDiscussionGroupResult,
    ListActiveStories,
    ListPinnedStories,
    ListProfileMusic,
    ListProfilePhotos,
)
from schemas.telegram_profile_snapshot import (
    TelegramActiveStories,
    TelegramPinnedStories,
    TelegramProfileMusic,
    TelegramProfilePhotos,
    TelegramProfileSnapshot,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: object) -> None:
    """Replace ``get_client`` with a coroutine that returns ``client``.

    Read paths now borrow from the per-account pool. Tests no longer need
    to stub the per-call ``telegram_client`` context manager.
    """

    async def fake_get_client(_account_id: str) -> object:
        return client

    async def fake_fetch(account_id: str):
        return MagicMock(session_name=account_id)

    monkeypatch.setattr("core.telegram_client._read.get_client", fake_get_client)
    monkeypatch.setattr("core.telegram_client._read.fetch_account", fake_fetch)


@pytest.mark.asyncio
async def test_get_user_profile_returns_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    requested: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            requested.append(request)
            if isinstance(request, GetFullUserRequest):
                return MagicMock(
                    full_user=MagicMock(about="Hi there"),
                    users=[
                        MagicMock(
                            first_name="Alice",
                            last_name="Liddell",
                            username="alice",
                            phone="79991234567",
                        ),
                    ],
                )
            return MagicMock()

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-1", GetUserProfile())

    assert isinstance(result, TelegramProfileSnapshot)
    assert result.first_name == "Alice"
    assert result.last_name == "Liddell"
    assert result.username == "alice"
    assert result.phone == "79991234567"
    assert result.bio == "Hi there"
    assert any(isinstance(req, GetFullUserRequest) for req in requested)


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
    """``StoryItem.views.views_count`` must land on the snapshot row.

    A story that omits view data (Telegram returns no ``views`` object for
    expired, unpinned stories) maps to ``None``, not a crash.
    """
    with_views = MagicMock(
        id=401,
        media=MagicMock(spec=MessageMediaPhoto),
        caption=None,
        views=MagicMock(views_count=137),
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
    assert result.items[1].views is None


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
async def test_list_profile_music_when_unsupported_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("core.telegram_client._read._MUSIC_API_AVAILABLE", False)
    monkeypatch.setattr("core.telegram_client._read.GetSavedMusicRequest", None)

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:  # pragma: no cover - never called
            msg = "client should not be called when music API absent"
            raise AssertionError(msg)

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-no-music-api", ListProfileMusic())

    assert isinstance(result, TelegramProfileMusic)
    assert result.items == []
    assert result.supported is False


@pytest.mark.asyncio
async def test_list_profile_music_when_supported_maps_audio_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("core.telegram_client._read._MUSIC_API_AVAILABLE", True)

    class FakeMusicRequest:
        def __init__(self, *, id: object, offset: int, limit: int, hash: int) -> None:  # noqa: A002 — mirrors Telethon's TL parameter
            self.id = id
            self.offset = offset
            self.limit = limit
            self.hash = hash

    monkeypatch.setattr("core.telegram_client._read.GetSavedMusicRequest", FakeMusicRequest)

    audio = DocumentAttributeAudio(
        duration=183,
        voice=False,
        title="Memorabilia",
        performer="The Heads",
        waveform=None,
    )
    document = MagicMock(id=555, attributes=[audio])

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, name: str) -> object:
            assert name == "me"
            return MagicMock(name="InputUserSelf")

        async def __call__(self, request: object) -> object:
            assert isinstance(request, FakeMusicRequest)
            assert request.offset == 0
            assert request.hash == 0
            return MagicMock(documents=[document])

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-music", ListProfileMusic())

    assert isinstance(result, TelegramProfileMusic)
    assert result.supported is True
    assert len(result.items) == 1
    track = result.items[0]
    assert track.file_id == 555
    assert track.title == "Memorabilia"
    assert track.performer == "The Heads"
    assert track.duration_seconds == 183


@pytest.mark.asyncio
async def test_list_profile_photos_maps_id_triple_and_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each photo must carry the InputPhoto id triple + a Unix-int date.

    Telethon hands us a ``datetime`` for ``photo.date``; the dispatcher has to
    flatten it to a plain int so the schema stays Pydantic-friendly and the UI
    can format without re-importing datetime.
    """
    photo_a = MagicMock(
        id=111,
        access_hash=22,
        file_reference=b"\x0a",
        date=datetime(2025, 3, 4, 12, 0, tzinfo=UTC),
    )
    photo_b = MagicMock(
        id=222,
        access_hash=33,
        file_reference=b"\x0b",
        date=datetime(2024, 1, 1, tzinfo=UTC),
    )
    photos_payload = MagicMock(photos=[photo_a, photo_b])
    requested: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            requested.append(request)
            return photos_payload

        async def download_media(self, _media: object, *, file: object, thumb: int) -> bytes:  # noqa: ARG002
            assert thumb == -1, "photo grid must fetch the largest preview, not the stripped thumb"
            return b"thumb"

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-photos", ListProfilePhotos(limit=24))

    assert isinstance(result, TelegramProfilePhotos)
    assert any(isinstance(req, GetUserPhotosRequest) for req in requested)
    assert [item.photo_id for item in result.items] == [111, 222]
    assert result.items[0].access_hash == 22
    assert result.items[0].file_reference == b"\x0a"
    assert result.items[0].thumb_bytes == b"thumb"
    assert result.items[0].date_unix == int(
        datetime(2025, 3, 4, 12, 0, tzinfo=UTC).timestamp(),
    )


@pytest.mark.asyncio
async def test_list_profile_photos_empty_account(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            return MagicMock(photos=[])

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-no-photos", ListProfilePhotos())

    assert isinstance(result, TelegramProfilePhotos)
    assert result.items == []


@pytest.mark.asyncio
async def test_list_profile_photos_thumb_failure_is_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One bad thumb must not blow up the whole grid.

    Mirrors the story-thumb safety net: ``download_media`` can fail on
    privacy-restricted or cache-evicted photos; the dispatcher swallows
    the RPCError and the card renders without an image instead.
    """
    photo = MagicMock(
        id=1,
        access_hash=2,
        file_reference=b"\x01",
        date=datetime(2024, 1, 1, tzinfo=UTC),
    )

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            return MagicMock(photos=[photo])

        async def download_media(self, _media: object, *, file: object, thumb: int) -> bytes:  # noqa: ARG002
            raise errors.RPCError(request=None, message="FILE_REFERENCE_EXPIRED", code=400)

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-bad-thumb", ListProfilePhotos())

    assert isinstance(result, TelegramProfilePhotos)
    assert len(result.items) == 1
    assert result.items[0].photo_id == 1
    assert result.items[0].thumb_bytes is None


@pytest.mark.asyncio
async def test_get_linked_discussion_group_present(monkeypatch: pytest.MonkeyPatch) -> None:
    from telethon.tl.functions.channels import GetFullChannelRequest  # noqa: PLC0415

    requested: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            requested.append(request)
            return MagicMock(full_chat=MagicMock(linked_chat_id=-100123))

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-linked", GetLinkedDiscussionGroup(channel="@news"))

    assert isinstance(result, LinkedDiscussionGroupResult)
    assert result.linked_chat_id == -100123
    assert result.comments_enabled is True
    assert any(isinstance(req, GetFullChannelRequest) for req in requested)


@pytest.mark.asyncio
async def test_get_linked_discussion_group_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            return MagicMock(full_chat=MagicMock(linked_chat_id=None))

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-nolink", GetLinkedDiscussionGroup(channel="@nocomments"))

    assert isinstance(result, LinkedDiscussionGroupResult)
    assert result.linked_chat_id is None
    assert result.comments_enabled is False


@pytest.mark.asyncio
async def test_check_messages_alive_reports_deleted_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``get_messages`` ``None`` for an id means that comment was deleted/gone."""
    from telethon.tl.functions.channels import GetFullChannelRequest  # noqa: PLC0415

    group = MagicMock(id=999)
    read_calls: list[list[int]] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            assert isinstance(request, GetFullChannelRequest)
            return MagicMock(full_chat=MagicMock(linked_chat_id=999), chats=[group])

        async def get_messages(self, entity: object, *, ids: list[int]) -> list[object | None]:
            assert entity is group  # reads the resolved linked discussion group
            read_calls.append(ids)
            return [None if mid == 2 else MagicMock() for mid in ids]

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-x", CheckMessagesAlive(channel="@news", message_ids=[1, 2, 3]))

    assert isinstance(result, CheckMessagesAliveResult)
    assert result.missing_ids == [2]
    assert read_calls == [[1, 2, 3]]


@pytest.mark.asyncio
async def test_check_messages_alive_no_linked_group_reports_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Comments disabled / unlinked → can't verify, so report nothing gone."""

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            return MagicMock(full_chat=MagicMock(linked_chat_id=None), chats=[])

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-x", CheckMessagesAlive(channel="@news", message_ids=[1, 2]))

    assert isinstance(result, CheckMessagesAliveResult)
    assert result.missing_ids == []


@pytest.mark.asyncio
async def test_check_messages_alive_unresolved_group_reports_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Linked id present but its entity missing from ChatFull → no false positives."""

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            return MagicMock(full_chat=MagicMock(linked_chat_id=999), chats=[MagicMock(id=111)])

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-x", CheckMessagesAlive(channel="@news", message_ids=[1]))

    assert isinstance(result, CheckMessagesAliveResult)
    assert result.missing_ids == []


@pytest.mark.asyncio
async def test_execute_read_unknown_account_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(_account_id: str):
        return None

    monkeypatch.setattr("core.telegram_client._read.fetch_account", fake_fetch)

    with pytest.raises(TelegramAccountNotFoundError):
        await execute_read("ghost", GetUserProfile())


@pytest.mark.asyncio
async def test_execute_read_flood_wait_wraps_telethon_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            raise errors.FloodWaitError(request=None, capture=42)

    _patch_client(monkeypatch, FakeClient())

    with pytest.raises(TelegramReadError) as exc_info:
        await execute_read("acc-flood", GetUserProfile())

    assert exc_info.value.reason == "FloodWait(42s)"


@pytest.mark.asyncio
async def test_execute_read_rpc_error_wraps_telethon_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            raise errors.RPCError(request=None, message="USER_DEACTIVATED", code=400)

    _patch_client(monkeypatch, FakeClient())

    with pytest.raises(TelegramReadError) as exc_info:
        await execute_read("acc-rpc", GetUserProfile())

    assert "RPC" in exc_info.value.reason


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
async def test_execute_read_many_opens_single_client_for_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a batch borrows the pool exactly ONCE for N actions.

    Originally the dialog opened 3 fresh Telethon clients in parallel and
    raced into ``OperationalError: database is locked``. Then ``execute_read_many``
    serialised into one per-call client. Now the pool keeps the client warm
    across batches, but the *single borrow per batch* invariant still
    holds — and it's tested at the seam (``get_client`` calls), not at the
    factory level which lives in the pool's own tests.
    """
    pool_borrows = 0
    handled: list[object] = []

    class FakeClient:
        async def get_input_entity(self, _name: str) -> object:
            return MagicMock()

        async def __call__(self, request: object) -> object:
            handled.append(request)
            if isinstance(request, GetFullUserRequest):
                return MagicMock(full_user=MagicMock(about=None), users=[MagicMock()])
            if isinstance(request, GetPinnedStoriesRequest):
                return MagicMock(stories=[])
            # GetSavedMusicRequest fallback
            return MagicMock(documents=[])

    shared_client = FakeClient()

    async def fake_get_client(_account_id: str) -> object:
        nonlocal pool_borrows
        pool_borrows += 1
        return shared_client

    async def fake_fetch(account_id: str):
        return MagicMock(session_name=account_id)

    monkeypatch.setattr("core.telegram_client._read.get_client", fake_get_client)
    monkeypatch.setattr("core.telegram_client._read.fetch_account", fake_fetch)

    results = await execute_read_many(
        "acc-batch",
        [GetUserProfile(), ListPinnedStories(), ListProfileMusic()],
    )

    assert pool_borrows == 1, "execute_read_many must borrow once per batch"
    assert len(results) == 3, "must return one result per action, in input order"
