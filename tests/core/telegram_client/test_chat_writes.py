"""``PostComment``'s reply aim, and what the chat-scoped writes put in the log."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from core.telegram_client import execute
from core.telegram_client._actions import _action_log_extra
from schemas.telegram_actions import CopyMessageMedia, PostComment, ReactToMessage
from tests.core.telegram_client.helpers import patch_action_client as _patch_client

if TYPE_CHECKING:
    from schemas.telegram_actions import TelegramAction


class _SendClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def connect(self) -> None:
        return None

    async def send_message(self, chat_id: int, text: str, *, reply_to: int | None) -> object:
        self.calls.append({"chat_id": chat_id, "text": text, "reply_to": reply_to})
        return MagicMock(id=101)


@pytest.mark.asyncio
async def test_post_comment_passes_reply_to(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one field that turns a list of sends into a staged conversation.

    ``CommentOnPost`` has its own ``reply_to`` against a different peer (a channel's
    linked group); this is the in-chat aim, and the two are not interchangeable.
    """
    client = _SendClient()
    _patch_client(monkeypatch, client)

    result = await execute("acc-1", PostComment(chat_id=900, text="hi", reply_to=55))

    assert (result.status, result.message_id) == ("ok", 101)
    assert client.calls == [{"chat_id": 900, "text": "hi", "reply_to": 55}]


@pytest.mark.asyncio
async def test_post_comment_without_a_reply_still_sends_the_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _SendClient()
    _patch_client(monkeypatch, client)

    await execute("acc-1", PostComment(chat_id=900, text="hi"))

    assert client.calls[0]["reply_to"] is None


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (
            PostComment(chat_id=900, text="hi", reply_to=55),
            {"chat_id": 900, "reply_to": 55},
        ),
        (
            ReactToMessage(chat_id=900, message_id=7, emoji="👍"),
            {"chat_id": 900, "message_id": 7},
        ),
        (
            CopyMessageMedia(
                chat_id=900,
                source_chat="1234",
                source_message_id=7,
                caption="secret words",
                reply_to=None,
            ),
            {"chat_id": 900, "source_chat": "1234", "reply_to": None},
        ),
    ],
)
def test_the_log_extra_names_the_aim_and_never_the_content(
    action: TelegramAction,
    expected: dict[str, object],
) -> None:
    """A reply and a plain send share one ``action_type``; only the log tells them apart.

    The caption is content and stays out, exactly as message text does everywhere
    else in this gateway.
    """
    assert _action_log_extra(action) == expected
