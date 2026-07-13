"""Tests for the warming-specific Telegram actions dispatched by ``execute``."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from telethon.tl.functions.account import UpdateStatusRequest
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.functions.stories import GetPeerStoriesRequest, ReadStoriesRequest
from telethon.tl.types import ChatReactionsNone, ChatReactionsSome, ReactionEmoji

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.repositories.logs import list_recent_logs
from core.telegram_client import execute
from schemas.telegram_actions import (
    ReactToPost,
    ReadChannel,
    SendDirectMessage,
    SetOnline,
    WatchPeerStories,
)

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


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: object) -> None:
    async def fake_get_client(_account_id: str) -> object:
        return client

    async def fake_fetch(account_id: str):
        return MagicMock(session_name=account_id)

    monkeypatch.setattr("core.telegram_client._actions.get_client", fake_get_client)
    monkeypatch.setattr("core.telegram_client._actions.fetch_account", fake_fetch)


@pytest.mark.asyncio
async def test_set_online_dispatches_update_status(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> None:
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-1", SetOnline(online=True))

    assert result.status == "ok"
    assert any(isinstance(req, UpdateStatusRequest) for req in captured)


@pytest.mark.asyncio
async def test_read_channel_marks_history_read(monkeypatch: pytest.MonkeyPatch) -> None:
    acks: list[int] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_messages(self, _channel: str, *, limit: int) -> list[object]:
            assert limit > 0
            return [MagicMock(id=5), MagicMock(id=9)]

        async def send_read_acknowledge(self, _channel: str, *, max_id: int) -> None:
            acks.append(max_id)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-2", ReadChannel(channel="@news", message_limit=10))

    assert result.status == "ok"
    assert acks == [9]


@pytest.mark.asyncio
async def test_read_channel_with_no_messages_skips_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    acks: list[int] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_messages(self, _channel: str, *, limit: int) -> list[object]:
            assert limit > 0
            return []

        async def send_read_acknowledge(self, _channel: str, *, max_id: int) -> None:
            acks.append(max_id)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-2b", ReadChannel(channel="@news"))

    assert result.status == "ok"
    assert acks == []


@pytest.mark.asyncio
async def test_react_to_post_sends_reaction(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_messages(self, _channel: str, *, limit: int) -> list[object]:
            assert limit > 0
            return [MagicMock(id=11), MagicMock(id=12)]

        async def get_input_entity(self, channel: str) -> str:
            return f"peer:{channel}"

        async def __call__(self, request: object) -> None:
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-3",
        ReactToPost(channel="@news", reactions=["👍", "🔥"], message_limit=20),
    )

    assert result.status == "ok"
    assert result.message_id in {11, 12}
    assert any(isinstance(req, SendReactionRequest) for req in captured)


@pytest.mark.asyncio
async def test_react_to_post_no_messages_returns_ok_without_reaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_messages(self, _channel: str, *, limit: int) -> list[object]:
            assert limit > 0
            return []

        async def get_input_entity(self, channel: str) -> str:  # pragma: no cover
            return channel

        async def __call__(self, request: object) -> None:  # pragma: no cover
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-3b", ReactToPost(channel="@news", reactions=["👍"]))

    assert result.status == "ok"
    assert result.message_id is None
    assert captured == []
    rows = await list_recent_logs(limit=20)
    react_rows = [r for r in rows if r.event == "telegram_react_to_post"]
    assert react_rows[0].extra["reaction_skip"] == "no_posts"


def _react_fake_client(captured: list[object], available: object) -> object:
    """A fake client whose channel exposes ``available`` reactions."""

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_messages(self, _channel: str, *, limit: int) -> list[object]:
            assert limit > 0
            return [MagicMock(id=11)]

        async def get_input_entity(self, channel: str) -> str:
            return f"peer:{channel}"

        async def __call__(self, request: object) -> object:
            captured.append(request)
            if isinstance(request, GetFullChannelRequest):
                return MagicMock(full_chat=MagicMock(available_reactions=available))
            return None

    return FakeClient()


def _sent_reactions(captured: list[object]) -> list[SendReactionRequest]:
    return [req for req in captured if isinstance(req, SendReactionRequest)]


@pytest.mark.asyncio
async def test_react_to_post_uses_only_channel_allowed_emoji(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only 🔥 is allowed by the channel, so that's the reaction sent (not 👍)."""
    captured: list[object] = []
    allowed = ChatReactionsSome(reactions=[ReactionEmoji(emoticon="🔥")])
    _patch_client(monkeypatch, _react_fake_client(captured, allowed))

    result = await execute("acc-3c", ReactToPost(channel="@durov", reactions=["👍", "🔥"]))

    assert result.status == "ok"
    sent = _sent_reactions(captured)
    assert len(sent) == 1
    reaction = sent[0].reaction
    assert reaction is not None
    first = reaction[0]
    assert isinstance(first, ReactionEmoji)
    assert first.emoticon == "🔥"


@pytest.mark.asyncio
async def test_react_to_post_falls_back_to_channel_emoji_when_none_of_ours_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """None of our set is allowed, but 😍 (non-negative) is — react with 😍 so it still lands."""
    captured: list[object] = []
    allowed = ChatReactionsSome(reactions=[ReactionEmoji(emoticon="😍")])
    _patch_client(monkeypatch, _react_fake_client(captured, allowed))

    result = await execute("acc-3d", ReactToPost(channel="@durov", reactions=["👍", "🔥"]))

    assert result.status == "ok"
    assert result.message_id == 11
    sent = _sent_reactions(captured)
    assert len(sent) == 1
    reaction = sent[0].reaction
    assert reaction is not None
    first = reaction[0]
    assert isinstance(first, ReactionEmoji)
    assert first.emoticon == "😍"


@pytest.mark.asyncio
async def test_react_to_post_matches_heart_ignoring_variation_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Config ❤️ (U+FE0F) matches Telegram's bare ❤ and is sent in the bare form."""
    captured: list[object] = []
    allowed = ChatReactionsSome(reactions=[ReactionEmoji(emoticon="❤")])
    _patch_client(monkeypatch, _react_fake_client(captured, allowed))

    result = await execute("acc-3f", ReactToPost(channel="@durov", reactions=["❤️", "👍"]))

    assert result.status == "ok"
    sent = _sent_reactions(captured)
    assert len(sent) == 1
    reaction = sent[0].reaction
    assert reaction is not None
    first = reaction[0]
    assert isinstance(first, ReactionEmoji)
    assert first.emoticon == "❤"


@pytest.mark.asyncio
async def test_react_to_post_skips_when_only_negative_reactions_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The channel permits only a blocklisted 👎 — skip rather than react negatively."""
    captured: list[object] = []
    allowed = ChatReactionsSome(reactions=[ReactionEmoji(emoticon="👎")])
    _patch_client(monkeypatch, _react_fake_client(captured, allowed))

    result = await execute("acc-3g", ReactToPost(channel="@durov", reactions=["👍", "🔥"]))

    assert result.status == "ok"
    assert result.message_id is None
    assert _sent_reactions(captured) == []


@pytest.mark.asyncio
async def test_react_to_post_logs_chosen_emoji_and_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The react log row records which emoji landed in which channel (card display)."""
    captured: list[object] = []
    allowed = ChatReactionsSome(reactions=[ReactionEmoji(emoticon="🔥")])
    _patch_client(monkeypatch, _react_fake_client(captured, allowed))

    result = await execute("acc-3h", ReactToPost(channel="@durov", reactions=["👍", "🔥"]))

    assert result.status == "ok"
    rows = await list_recent_logs(limit=20)
    react_rows = [r for r in rows if r.event == "telegram_react_to_post"]
    assert react_rows
    assert react_rows[0].extra["channel"] == "@durov"
    assert react_rows[0].extra["reaction"] == "🔥"


@pytest.mark.asyncio
async def test_react_to_post_skips_when_reactions_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reactions are off on the channel — skip cleanly, never SendReactionRequest."""
    captured: list[object] = []
    _patch_client(monkeypatch, _react_fake_client(captured, ChatReactionsNone()))

    result = await execute("acc-3e", ReactToPost(channel="@durov", reactions=["👍", "🔥"]))

    assert result.status == "ok"
    assert result.message_id is None
    assert _sent_reactions(captured) == []
    rows = await list_recent_logs(limit=20)
    react_rows = [r for r in rows if r.event == "telegram_react_to_post"]
    assert react_rows[0].extra["reaction_skip"] == "no_emoji"


@pytest.mark.asyncio
async def test_send_dm_returns_message_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "typing_simulation_enabled", False)

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def send_message(self, user_id: int, text: str) -> object:
            assert user_id == 555
            assert text == "hello"
            return MagicMock(id=88)

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-4", SendDirectMessage(user_id=555, text="hello"))

    assert result.status == "ok"
    assert result.message_id == 88


@pytest.mark.asyncio
async def test_watch_peer_stories_marks_up_to_newest(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, peer: str) -> str:
            return f"peer:{peer}"

        async def __call__(self, request: object) -> object:
            captured.append(request)
            if isinstance(request, GetPeerStoriesRequest):
                return MagicMock(stories=MagicMock(stories=[MagicMock(id=3), MagicMock(id=7)]))
            return None

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-5", WatchPeerStories(peer="@news"))

    assert result.status == "ok"
    reads = [req for req in captured if isinstance(req, ReadStoriesRequest)]
    assert reads
    assert reads[0].max_id == 7
    rows = await list_recent_logs(limit=20)
    watch_rows = [r for r in rows if r.event == "telegram_watch_peer_stories"]
    assert watch_rows[0].extra["stories_seen"] == 2


@pytest.mark.asyncio
async def test_watch_peer_stories_no_stories_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, peer: str) -> str:
            return peer

        async def __call__(self, request: object) -> object:
            captured.append(request)
            if isinstance(request, GetPeerStoriesRequest):
                return MagicMock(stories=MagicMock(stories=[]))
            return None  # pragma: no cover - no ReadStories when there are no stories

    _patch_client(monkeypatch, FakeClient())

    result = await execute("acc-5b", WatchPeerStories(peer="@news"))

    assert result.status == "ok"
    assert not any(isinstance(req, ReadStoriesRequest) for req in captured)
    rows = await list_recent_logs(limit=20)
    watch_rows = [r for r in rows if r.event == "telegram_watch_peer_stories"]
    assert watch_rows[0].extra["stories_seen"] == 0
