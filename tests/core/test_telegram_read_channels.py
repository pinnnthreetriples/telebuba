"""Tests for the channel read dispatchers (``_read_channels.py``)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from telethon import errors
from telethon.tl.functions.channels import CheckUsernameRequest, GetFullChannelRequest
from telethon.tl.types import InputChannelEmpty

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.telegram_client import execute_read
from schemas.telegram_actions import (
    CheckChannelUsername,
    GetOwnChannel,
    ListChannelPosts,
    ListOwnChannels,
)
from schemas.telegram_actions_channels import (
    ChannelUsernameCheck,
    TelegramChannelPosts,
    TelegramOwnChannelDetail,
    TelegramOwnChannels,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
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
    """Replace ``get_client`` with a coroutine that returns ``client``."""

    async def fake_get_client(_account_id: str) -> object:
        return client

    async def fake_fetch(account_id: str):
        return MagicMock(session_name=account_id)

    monkeypatch.setattr("core.telegram_client._read.get_client", fake_get_client)
    monkeypatch.setattr("core.telegram_client._read.fetch_account", fake_fetch)


def _entity(**kwargs: object) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)


@pytest.mark.asyncio
async def test_list_own_channels_filters_creator_broadcast_including_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only creator+broadcast entities survive; private (no username) included."""
    dialogs = [
        # Owned public channel.
        SimpleNamespace(
            entity=_entity(
                broadcast=True,
                creator=True,
                id=100,
                title="Mine",
                username="mine",
                participants_count=12,
            ),
        ),
        # Owned PRIVATE channel — no username, must still be listed.
        SimpleNamespace(
            entity=_entity(
                broadcast=True,
                creator=True,
                id=200,
                title="Secret",
                username=None,
                participants_count=None,
            ),
        ),
        # Subscribed (not created) channel — filtered out.
        SimpleNamespace(
            entity=_entity(broadcast=True, creator=False, id=300, title="Theirs"),
        ),
        # Owned megagroup (not broadcast) — filtered out.
        SimpleNamespace(
            entity=_entity(broadcast=False, creator=True, id=400, title="Group"),
        ),
        # A user dialog with none of the flags.
        SimpleNamespace(entity=_entity(id=500)),
    ]
    scanned_limits: list[int] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        def iter_dialogs(self, *, limit: int) -> AsyncIterator[object]:
            scanned_limits.append(limit)

            async def gen() -> AsyncIterator[object]:
                for dialog in dialogs:
                    yield dialog

            return gen()

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-list", ListOwnChannels())

    assert isinstance(result, TelegramOwnChannels)
    assert [item.channel_id for item in result.items] == [100, 200]
    assert result.items[0].username == "mine"
    assert result.items[0].participants_count == 12
    assert result.items[1].username is None
    assert result.items[1].title == "Secret"
    # The dialog scan depth comes from config, not the action limit.
    assert scanned_limits == [settings.channels.dialogs_scan_limit]


@pytest.mark.asyncio
async def test_list_own_channels_stops_at_action_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialogs = [
        SimpleNamespace(entity=_entity(broadcast=True, creator=True, id=i, title=f"C{i}"))
        for i in range(1, 4)
    ]

    class FakeClient:
        async def connect(self) -> None:
            return None

        def iter_dialogs(self, *, limit: int) -> AsyncIterator[object]:  # noqa: ARG002
            async def gen() -> AsyncIterator[object]:
                for dialog in dialogs:
                    yield dialog

            return gen()

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-limit", ListOwnChannels(limit=2))

    assert isinstance(result, TelegramOwnChannels)
    assert [item.channel_id for item in result.items] == [1, 2]


@pytest.mark.asyncio
async def test_get_own_channel_maps_about_and_participants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            requested.append(request)
            return SimpleNamespace(
                full_chat=SimpleNamespace(about="All about it", participants_count=77),
                chats=[SimpleNamespace(title="Mine", username="mine")],
            )

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-detail", GetOwnChannel(channel_id=100))

    assert isinstance(result, TelegramOwnChannelDetail)
    assert result.channel_id == 100
    assert result.title == "Mine"
    assert result.username == "mine"
    assert result.about == "All about it"
    assert result.participants_count == 77
    assert any(isinstance(r, GetFullChannelRequest) for r in requested)


@pytest.mark.asyncio
async def test_get_own_channel_tolerates_missing_chats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            return SimpleNamespace(
                full_chat=SimpleNamespace(about="", participants_count=None),
                chats=[],
            )

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-bare", GetOwnChannel(channel_id=100))

    assert isinstance(result, TelegramOwnChannelDetail)
    assert result.title == ""
    assert result.username is None


@pytest.mark.asyncio
async def test_list_channel_posts_maps_media_kind_and_views(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    date = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    messages = [
        SimpleNamespace(
            id=40,
            date=date,
            message="photo post",
            photo=object(),
            video=None,
            media=object(),
            views=100,
        ),
        SimpleNamespace(
            id=30,
            date=date,
            message="video post",
            photo=None,
            video=object(),
            media=object(),
            views=200,
        ),
        SimpleNamespace(
            id=20,
            date=date,
            message="poll post",
            photo=None,
            video=None,
            media=object(),
            views=None,
        ),
        SimpleNamespace(
            id=10, date=date, message="text post", photo=None, video=None, media=None, views=5
        ),
        # An id-less entry (service message placeholder) is dropped.
        SimpleNamespace(
            id=0, date=None, message="", photo=None, video=None, media=None, views=None
        ),
    ]
    captured: dict[str, object] = {}

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_messages(self, _peer: object, *, limit: int, offset_id: int) -> object:
            captured["limit"] = limit
            captured["offset_id"] = offset_id
            return messages

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read(
        "acc-posts",
        ListChannelPosts(channel_id=100, limit=4, offset_id=41),
    )

    assert isinstance(result, TelegramChannelPosts)
    assert captured == {"limit": 4, "offset_id": 41}
    kinds = [item.media_kind for item in result.items]
    assert kinds == ["photo", "video", "other", "none"]
    assert [item.post_id for item in result.items] == [40, 30, 20, 10]
    assert [item.views for item in result.items] == [100, 200, None, 5]
    assert result.items[0].date_unix == int(date.timestamp())
    assert result.items[3].text == "text post"


@pytest.mark.asyncio
async def test_list_channel_posts_coerces_odd_dates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An int date passes through; a missing date flattens to 0."""
    messages = [
        SimpleNamespace(
            id=2, date=1_750_000_000, message="", photo=None, video=None, media=None, views=None
        ),
        SimpleNamespace(
            id=1, date=None, message="", photo=None, video=None, media=None, views=None
        ),
    ]

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_messages(self, _peer: object, *, limit: int, offset_id: int) -> object:  # noqa: ARG002
            return messages

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read("acc-dates", ListChannelPosts(channel_id=100))

    assert isinstance(result, TelegramChannelPosts)
    assert [item.date_unix for item in result.items] == [1_750_000_000, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "available", "code"),
    [
        (True, True, None),
        (False, False, "channel_username_occupied"),
    ],
)
async def test_check_channel_username_maps_bool_answers(
    monkeypatch: pytest.MonkeyPatch,
    *,
    answer: bool,
    available: bool,
    code: str | None,
) -> None:
    requested: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            requested.append(request)
            return answer

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read(
        "acc-check",
        CheckChannelUsername(username="fresh_handle"),
    )

    assert isinstance(result, ChannelUsernameCheck)
    assert result.available is available
    assert result.code == code
    check = next(r for r in requested if isinstance(r, CheckUsernameRequest))
    assert isinstance(check.channel, InputChannelEmpty)
    assert check.username == "fresh_handle"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raised", "code"),
    [
        (errors.UsernameInvalidError(request=None), "channel_username_invalid"),
        (errors.UsernamePurchaseAvailableError(request=None), "channel_username_occupied"),
    ],
)
async def test_check_channel_username_maps_rpc_refusals_to_codes(
    monkeypatch: pytest.MonkeyPatch,
    raised: Exception,
    code: str,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            raise raised

    _patch_client(monkeypatch, FakeClient())

    result = await execute_read(
        "acc-check-bad",
        CheckChannelUsername(username="some_handle"),
    )

    assert isinstance(result, ChannelUsernameCheck)
    assert result.available is False
    assert result.code == code
