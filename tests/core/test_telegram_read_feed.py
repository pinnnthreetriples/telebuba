"""``read_channel`` reports which read posts a warming extra can act on, and skips ride back.

The read classifies with the very tests the poll vote and the media playback apply, so
the extras step only aims those at a post that has one. A skip (``warm_skip``) reaches
``ActionResult`` so the step can tell "ran, nothing to do" from an action performed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from telethon.tl.functions.channels import GetMessagesRequest
from telethon.tl.types import MessageMediaPhoto

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.telegram_client import execute
from schemas.telegram_actions import ReadChannel, WarmVoteInPoll
from tests.core.telegram_client.helpers import patch_action_client as _patch_client
from tests.core.test_telegram_warm_download import _round, _video, _voice
from tests.core.test_telegram_warm_media import _poll_media

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


class _FeedClient:
    def __init__(self, posts: list[object]) -> None:
        self._posts = posts
        self.acks: list[int] = []

    async def connect(self) -> None:
        return None

    async def get_messages(self, _channel: str, *, limit: int) -> list[object]:
        assert limit > 0
        return self._posts

    async def send_read_acknowledge(self, _channel: str, *, max_id: int) -> None:
        self.acks.append(max_id)


def _post(message_id: int, media: object = None) -> MagicMock:
    return MagicMock(id=message_id, media=media)


@pytest.mark.asyncio
async def test_read_channel_reports_the_posts_an_extra_can_act_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posts: list[object] = [
        _post(1, _poll_media()),
        _post(2, _video()),
        _post(3, _voice()),
        _post(4, _round()),  # a round video message plays like a voice note
        _post(5, MagicMock(spec=MessageMediaPhoto)),
        _post(6),
    ]
    client = _FeedClient(posts)
    _patch_client(monkeypatch, client)

    result = await execute("acc-f1", ReadChannel(channel="@feed", message_limit=20))

    assert result.status == "ok"
    assert result.recent_message_ids == ["1", "2", "3", "4", "5", "6"]
    assert result.recent_media_kinds == {"1": "poll", "2": "video", "3": "voice", "4": "voice"}
    assert client.acks == [6]


@pytest.mark.parametrize(
    "media",
    [
        _poll_media(closed=True),
        _poll_media(public_voters=True),
        _poll_media(quiz=True),
        _poll_media(chosen=1),
    ],
    ids=["closed", "public", "quiz", "already_voted"],
)
@pytest.mark.asyncio
async def test_read_channel_does_not_offer_a_poll_the_vote_would_refuse(
    monkeypatch: pytest.MonkeyPatch, media: object
) -> None:
    _patch_client(monkeypatch, _FeedClient([_post(1, media)]))

    result = await execute("acc-f2", ReadChannel(channel="@feed", message_limit=20))

    assert result.recent_media_kinds == {}


@pytest.mark.asyncio
async def test_a_skip_rides_back_on_the_action_result(monkeypatch: pytest.MonkeyPatch) -> None:
    class _NoPollClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, username: str) -> str:
            return f"peer:{username}"

        async def __call__(self, request: object) -> object:
            assert isinstance(request, GetMessagesRequest)
            return MagicMock(messages=[_post(10)])

    _patch_client(monkeypatch, _NoPollClient())

    action = WarmVoteInPoll(channel="@feed", message_ids=[10], option_index=0)
    result = await execute("acc-f3", action)

    assert result.status == "ok"
    assert result.warm_skip == "no_poll"
    assert result.recent_media_kinds is None
