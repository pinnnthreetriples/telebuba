"""The gateway's own log rows name a private chat by a fingerprint, never by its invite.

Every ``telegram_*`` row carries the channel the action was aimed at. For a join by invite
that is the invite itself, and the row outlives the run in the operator's logs.
"""

from __future__ import annotations

import pytest
from telethon import errors

from core.channel_tokens import loggable_folder, loggable_ref
from core.db import list_recent_logs
from core.telegram_client import execute
from core.telegram_client._actions import _action_log_extra
from core.telegram_client._broadcast import broadcast_log_extra
from schemas.telegram_actions import (
    BroadcastForwardPost,
    BroadcastJoinChatlist,
    BroadcastSendMessage,
    CommentOnPost,
    JoinChannel,
)
from tests.core.telegram_client.helpers import patch_action_client as _patch_client

_HASH = "AbCdEfGhIjK0123"
_INVITE = f"+{_HASH}"
_LINK = f"https://t.me/+{_HASH}"
_SLUG = "FolderSlug0123"


async def _logged_channels(fragment: str) -> list[object]:
    return [e.extra.get("channel") for e in await list_recent_logs(limit=50) if fragment in e.event]


def _client_raising(error: Exception | None) -> object:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> None:
            if error is not None:
                raise error

    return FakeClient()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "fragment"),
    [
        (None, "telegram_join_channel"),
        (errors.UserAlreadyParticipantError(request=None), "already_participant"),
        (errors.InviteRequestSentError(request=None), "by_request"),
        (errors.InviteHashExpiredError(request=None), "join_channel_failed"),
    ],
)
async def test_a_join_by_invite_logs_the_fingerprint_on_every_outcome(
    monkeypatch: pytest.MonkeyPatch, error: Exception | None, fragment: str
) -> None:
    _patch_client(monkeypatch, _client_raising(error))

    await execute("acc", JoinChannel(channel=_LINK), domain="chat_broadcast")

    assert await _logged_channels(fragment) == [loggable_ref(_INVITE)]
    assert _HASH not in repr(await list_recent_logs(limit=50))


def test_a_public_channel_is_still_logged_by_its_handle() -> None:
    assert _action_log_extra(JoinChannel(channel="@news")) == {"channel": "@news"}


def test_a_comment_aimed_at_a_private_channel_logs_the_fingerprint() -> None:
    action = CommentOnPost(channel=_INVITE, post_id=5, text="hi")

    assert _action_log_extra(action)["channel"] == loggable_ref(_INVITE)


def test_the_broadcast_rows_name_a_private_chat_by_its_fingerprint() -> None:
    send = BroadcastSendMessage(chat=_LINK, text="never logged")
    forward = BroadcastForwardPost(chat=_LINK, channel="mychannel", message_id=3)
    folder = BroadcastJoinChatlist(slug=_SLUG, peer_id=9)

    assert broadcast_log_extra(send) == {"chat": loggable_ref(_INVITE), "has_photo": False}
    assert broadcast_log_extra(forward) == {
        "chat": loggable_ref(_INVITE),
        "channel": "mychannel",
        "post_id": 3,
    }
    assert broadcast_log_extra(folder) == {"slug": loggable_folder(_SLUG), "peer_id": 9}
    assert _HASH not in repr([broadcast_log_extra(a) for a in (send, forward)])
    assert _SLUG not in repr(broadcast_log_extra(folder))


def test_a_broadcast_write_into_a_known_chat_is_logged_by_its_handle_or_id() -> None:
    assert broadcast_log_extra(BroadcastSendMessage(chat="alpha", text="x"))["chat"] == "alpha"
    assert broadcast_log_extra(BroadcastSendMessage(chat="1234", text="x"))["chat"] == "1234"
