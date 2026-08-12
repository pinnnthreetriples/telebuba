"""Replying to a human comment: the thread read and the reply-aimed write.

The two sides share one id space — ``ReadPostCommentsResult`` hands out DISCUSSION-GROUP
message ids and ``CommentOnPost.reply_to`` takes one back — so both live in one file, and
the assertions are mostly about which peer each call addresses.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto

from core.telegram_client import execute, execute_read
from schemas.telegram_actions import CommentOnPost, ReadPostComments
from schemas.telegram_actions_comments import ReadPostCommentsResult
from tests.core.telegram_client.helpers import (
    patch_action_client,
    patch_read_client,
)

_GROUP = MagicMock(id=999)


def _post(*, text: str = "the post", media: object = None, grouped_id: object = None):
    """A channel post as Telethon hands it over — media attrs explicit, never auto-mocked."""
    return MagicMock(id=55, message=text, media=media, grouped_id=grouped_id)


def _thread_message(message_id: int, text: str, sender_id: int | None = 7):
    return MagicMock(id=message_id, message=text, sender_id=sender_id)


class _ReadClient:
    """Answers the post read, the linked-group resolve and the thread read, in that order."""

    def __init__(self, *, post: object, thread: list[object], linked: int | None = 999) -> None:
        self._post = post
        self._thread = thread
        self._linked = linked
        self.thread_calls: list[dict[str, object]] = []

    async def connect(self) -> None:
        return None

    async def __call__(self, request: object) -> object:
        assert isinstance(request, GetFullChannelRequest)
        return MagicMock(full_chat=MagicMock(linked_chat_id=self._linked), chats=[_GROUP])

    async def get_messages(self, entity: object, *, ids: int) -> object:
        assert entity == "@news"
        assert ids == 55
        return self._post

    async def iter_messages(self, entity: object, **kwargs: object):
        self.thread_calls.append({"entity": entity, **kwargs})
        for message in self._thread:
            yield message


@pytest.mark.asyncio
async def test_read_post_comments_returns_thread_oldest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Order is Telegram's, asked for with ``reverse=True``; the gateway must not re-sort."""
    client = _ReadClient(
        post=_post(),
        thread=[
            _thread_message(101, "first human", sender_id=11),
            _thread_message(102, "second human", sender_id=12),
            _thread_message(103, "third human", sender_id=13),
        ],
    )
    patch_read_client(monkeypatch, client)

    result = await execute_read("acc-r", ReadPostComments(channel="@news", post_id=55, limit=20))

    assert isinstance(result, ReadPostCommentsResult)
    assert [c.message_id for c in result.comments] == [101, 102, 103]
    assert [c.text for c in result.comments] == ["first human", "second human", "third human"]
    assert [c.sender_id for c in result.comments] == [11, 12, 13]
    # getReplies is asked of the CHANNEL and the post id; the ids above are group ids.
    assert client.thread_calls == [
        {"entity": "@news", "reply_to": 55, "reverse": True, "limit": 20},
    ]


@pytest.mark.asyncio
async def test_read_post_comments_carries_the_post_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reply prompt needs the post, and the DB never stored it — one action, both halves."""
    client = _ReadClient(
        post=_post(text="look at this", media=MagicMock(spec=MessageMediaPhoto)),
        thread=[_thread_message(101, "nice")],
    )
    patch_read_client(monkeypatch, client)

    result = await execute_read("acc-r", ReadPostComments(channel="@news", post_id=55))

    assert isinstance(result, ReadPostCommentsResult)
    assert result.post_text == "look at this"
    assert result.post_media_kind == "photo"
    assert result.post_missing is False


@pytest.mark.asyncio
async def test_read_post_comments_classifies_media_like_the_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same ``_media_kind`` the push path uses: a document is ``other``, not a photo."""
    client = _ReadClient(post=_post(media=MagicMock(spec=MessageMediaDocument)), thread=[])
    patch_read_client(monkeypatch, client)

    result = await execute_read("acc-r", ReadPostComments(channel="@news", post_id=55))

    assert isinstance(result, ReadPostCommentsResult)
    assert result.post_media_kind == "other"


@pytest.mark.asyncio
async def test_read_post_comments_no_linked_group_is_empty_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Comments off → ``getReplies`` would raise; "nobody commented" must not be an exception."""
    client = _ReadClient(post=_post(), thread=[_thread_message(101, "unreachable")], linked=None)
    patch_read_client(monkeypatch, client)

    result = await execute_read("acc-r", ReadPostComments(channel="@news", post_id=55))

    assert isinstance(result, ReadPostCommentsResult)
    assert result.comments == []
    assert result.post_text == "the post"
    assert result.post_missing is False
    assert client.thread_calls == []  # the thread read is never attempted


@pytest.mark.asyncio
async def test_read_post_comments_empty_thread_is_a_normal_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _ReadClient(post=_post(), thread=[])
    patch_read_client(monkeypatch, client)

    result = await execute_read("acc-r", ReadPostComments(channel="@news", post_id=55))

    assert isinstance(result, ReadPostCommentsResult)
    assert result.comments == []


@pytest.mark.asyncio
async def test_read_post_comments_deleted_post_reports_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deleted while the attempt was parked: the caller drops the attempt, nobody raises."""
    client = _ReadClient(post=None, thread=[_thread_message(101, "orphan")])
    patch_read_client(monkeypatch, client)

    result = await execute_read("acc-r", ReadPostComments(channel="@news", post_id=55))

    assert isinstance(result, ReadPostCommentsResult)
    assert result.post_missing is True
    assert result.comments == []
    assert result.post_media_kind is None
    assert client.thread_calls == []  # no group resolve, no thread read


@pytest.mark.asyncio
async def test_comment_on_post_without_reply_to_still_uses_comment_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the default path is unchanged sugar — no group resolve, no ``reply_to``."""
    calls: list[dict[str, object]] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            pytest.fail(f"top-level comment must not resolve the group: {type(request).__name__}")

        async def send_message(self, entity: object, text: str, **kwargs: object) -> object:
            calls.append({"entity": entity, "text": text, **kwargs})
            return MagicMock(id=8181)

    patch_action_client(monkeypatch, FakeClient())

    result = await execute("acc-c", CommentOnPost(channel="@news", post_id=55, text="great post"))

    assert result.status == "ok"
    assert result.message_id == 8181
    assert calls == [{"entity": "@news", "text": "great post", "comment_to": 55}]


@pytest.mark.asyncio
async def test_comment_on_post_with_reply_to_sends_into_the_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``comment_to`` would win over ``reply_to``, so the reply addresses the group itself."""
    calls: list[dict[str, object]] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            assert isinstance(request, GetFullChannelRequest)
            return MagicMock(full_chat=MagicMock(linked_chat_id=999), chats=[_GROUP])

        async def send_message(self, entity: object, text: str, **kwargs: object) -> object:
            calls.append({"entity": entity, "text": text, **kwargs})
            return MagicMock(id=9090)

    patch_action_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-c",
        CommentOnPost(channel="@news", post_id=55, text="agreed", reply_to=101),
    )

    assert result.status == "ok"
    assert result.message_id == 9090
    assert calls == [{"entity": _GROUP, "text": "agreed", "reply_to": 101}]


@pytest.mark.asyncio
async def test_comment_on_post_reply_to_fails_when_group_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No aim, no send: degrading to a top-level comment would write first and call it a reply.

    ``reply_to`` is set only by ``comment_mode='reply'``, whose whole point is not being the
    first commenter — and the fallback was invisible, since both the wait's own
    ``neurocomment_reply_to_human`` and this action's ``reply_to`` extra still claimed a reply.
    """
    calls: list[dict[str, object]] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            return MagicMock(full_chat=MagicMock(linked_chat_id=None), chats=[])

        async def send_message(self, entity: object, text: str, **kwargs: object) -> object:
            calls.append({"entity": entity, "text": text, **kwargs})
            return MagicMock(id=7070)

    patch_action_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-c",
        CommentOnPost(channel="@news", post_id=55, text="agreed", reply_to=101),
    )

    # A refused send like any other, so the caller's outcome ladder classifies it and the row
    # is failed — not a comment nobody asked for.
    assert result.status == "failed"
    assert result.error_type == "ValueError"
    assert calls == []
